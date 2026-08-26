#!/usr/bin/env python3
"""
Servidor proxy local para evitar CORS con las APIs de Rama Judicial.
Ejecutar: python proxy_server.py
Luego abrir: http://localhost:8000

Endpoints disponibles:
  /api/rama/...           -> API de consulta de procesos
  /api/publicaciones/...  -> API de publicaciones procesales
  /api/documento/...      -> Descarga de documentos PDF
"""

import http.server
import socketserver
import urllib.request
import urllib.error
import urllib.parse
import json
import ssl
import re
import os
from html.parser import HTMLParser
import http.cookiejar

PORT = 8000

# Cookie jar global para mantener sesión con publicaciones procesales
cookie_jar = http.cookiejar.CookieJar()

# Base URL para documentos
DOCS_BASE_URL = 'https://publicacionesprocesales.ramajudicial.gov.co'

class PublicacionesParser(HTMLParser):
    """Parser para extraer publicaciones del HTML de la Rama Judicial"""
    
    def __init__(self):
        super().__init__()
        self.publicaciones = []
        self.current_pub = None
        self.in_result_row = False
        self.in_title = False
        self.in_date = False
        self.in_category = False
        self.in_link = False
        self.capture_text = False
        self.current_text = ""
        self.depth = 0
        
    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get('class', '')
        
        # Detectar filas de resultados (asset-abstract, search-result, etc.)
        if tag == 'div' and any(c in class_name for c in ['asset-abstract', 'search-result', 'asset-entry']):
            self.in_result_row = True
            self.current_pub = {'titulo': '', 'fecha': '', 'categoria': '', 'enlace': '', 'detalles': ''}
            
        # Detectar título de publicación
        if self.in_result_row:
            if tag in ['h3', 'h4'] or (tag == 'a' and 'asset-title' in class_name):
                self.in_title = True
                self.capture_text = True
                if tag == 'a' and attrs_dict.get('href'):
                    href = attrs_dict.get('href', '')
                    if href.startswith('/'):
                        href = 'https://publicacionesprocesales.ramajudicial.gov.co' + href
                    self.current_pub['enlace'] = href
                    
            # Detectar fecha
            if 'fecha' in class_name.lower() or 'date' in class_name.lower():
                self.in_date = True
                self.capture_text = True
                
            # Detectar categoría
            if 'category' in class_name.lower() or 'categoria' in class_name.lower():
                self.in_category = True
                self.capture_text = True
                
            # Detectar enlaces a documentos
            if tag == 'a':
                href = attrs_dict.get('href', '')
                if href and ('.pdf' in href.lower() or 'document' in href.lower()):
                    if href.startswith('/'):
                        href = 'https://publicacionesprocesales.ramajudicial.gov.co' + href
                    self.current_pub['enlace'] = href
                    
    def handle_endtag(self, tag):
        if self.capture_text and self.current_pub:
            text = self.current_text.strip()
            if text:
                if self.in_title:
                    self.current_pub['titulo'] = text
                elif self.in_date:
                    self.current_pub['fecha'] = text
                elif self.in_category:
                    self.current_pub['categoria'] = text
                    
        self.capture_text = False
        self.current_text = ""
        self.in_title = False
        self.in_date = False
        self.in_category = False
        
        if tag == 'div' and self.in_result_row and self.current_pub:
            if self.current_pub.get('titulo') or self.current_pub.get('fecha'):
                self.publicaciones.append(self.current_pub)
            self.current_pub = None
            self.in_result_row = False
            
    def handle_data(self, data):
        if self.capture_text:
            self.current_text += data


def parse_publicaciones_html(html_content):
    """Parsear HTML y extraer publicaciones con sus documentos PDF"""
    publicaciones = []
    documentos = []
    
    # ===== EXTRAER DOCUMENTOS PDF =====
    # Patrón para enlaces a documentos PDF
    # Ejemplo: /documents/6098902/254280154/Estado+77+del+25+de+Agosto+de+2026+%281%29.pdf/12cfd2e5-35d8-9cc8-c252-73eabebb96ad
    pattern_pdf_link = r'href=["\']([^"\']*\.pdf[^"\']*)["\']'
    pdf_matches = re.findall(pattern_pdf_link, html_content, re.IGNORECASE)
    
    for pdf_url in pdf_matches:
        # Construir URL completa si es relativa
        if pdf_url.startswith('/'):
            full_url = DOCS_BASE_URL + pdf_url
        else:
            full_url = pdf_url
        
        # Extraer nombre del archivo de la URL
        # El nombre está entre el último / antes de .pdf y .pdf
        nombre_match = re.search(r'/([^/]+\.pdf)', pdf_url, re.IGNORECASE)
        if nombre_match:
            nombre_encoded = nombre_match.group(1)
            # Decodificar URL encoding
            nombre = urllib.parse.unquote(nombre_encoded).replace('+', ' ')
        else:
            nombre = 'documento.pdf'
        
        # Extraer fecha si está en el nombre
        fecha_match = re.search(r'(\d+)\s+de\s+(\w+)\s+de\s+(\d{4})', nombre, re.IGNORECASE)
        fecha = ''
        if fecha_match:
            fecha = f'{fecha_match.group(1)} de {fecha_match.group(2)} de {fecha_match.group(3)}'
        
        doc = {
            'nombre': nombre,
            'url': full_url,
            'fecha': fecha,
            'tipo': 'PDF'
        }
        
        # Evitar duplicados
        if not any(d.get('url') == doc['url'] for d in documentos):
            documentos.append(doc)
    
    # ===== EXTRAER DE TABLAS DE DOCUMENTOS =====
    # Buscar filas de tabla con documentos
    # Patrón: <tr>...<a href="...pdf">nombre.pdf</a>...<td>fecha</td>...</tr>
    pattern_doc_row = r'<tr[^>]*>.*?<a[^>]*href=["\']([^"\']*\.pdf[^"\']*)["\'][^>]*>([^<]+)</a>.*?</tr>'
    doc_rows = re.findall(pattern_doc_row, html_content, re.IGNORECASE | re.DOTALL)
    
    for url, nombre in doc_rows:
        if url.startswith('/'):
            full_url = DOCS_BASE_URL + url
        else:
            full_url = url
            
        nombre_limpio = re.sub(r'<[^>]+>', '', nombre).strip()
        
        # Buscar fecha en la misma fila
        fecha = ''
        row_match = re.search(rf'<tr[^>]*>.*?{re.escape(url)}.*?</tr>', html_content, re.IGNORECASE | re.DOTALL)
        if row_match:
            fecha_match = re.search(r'(\d{2}-\w{3}-\d{4}\s+\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2})', row_match.group(0))
            if fecha_match:
                fecha = fecha_match.group(1)
        
        doc = {
            'nombre': nombre_limpio,
            'url': full_url,
            'fecha': fecha,
            'tipo': 'PDF'
        }
        
        if not any(d.get('url') == doc['url'] for d in documentos):
            documentos.append(doc)
    
    # ===== EXTRAER PUBLICACIONES (resumen) =====
    # Patrón 1: Notificación por Estado
    pattern_notif = r'Notificación por Estado[^<]*No[.\s]*(\d+)[^<]*de[^<]*(\d+\s+de\s+\w+\s+de\s+\d+)'
    matches = re.findall(pattern_notif, html_content, re.IGNORECASE)
    for num, fecha in matches:
        publicaciones.append({
            'titulo': f'Notificación por Estado No.{num}',
            'fecha': fecha.strip(),
            'tipo': 'Notificación por Estado',
            'numero': num
        })
    
    # Patrón 2: ESTADO N° XX DEL fecha
    pattern_estado = r'ESTADO\s+N[°º]?\s*(\d+)\s+DEL\s+(\d+\s+DE\s+\w+\s+DE\s+\d{4})'
    estados = re.findall(pattern_estado, html_content, re.IGNORECASE)
    for num, fecha in estados:
        pub = {
            'titulo': f'Estado N° {num} del {fecha}',
            'fecha': fecha.strip(),
            'tipo': 'Estado',
            'numero': num
        }
        if not any(p.get('titulo') == pub['titulo'] for p in publicaciones):
            publicaciones.append(pub)
    
    # Patrón 3: Asset entries
    pattern_asset = r'<div[^>]*class="[^"]*asset-abstract[^"]*"[^>]*>(.*?)</div>'
    assets = re.findall(pattern_asset, html_content, re.IGNORECASE | re.DOTALL)
    
    for asset in assets:
        title_match = re.search(r'<a[^>]*>([^<]+)</a>', asset)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d+\s+de\s+\w+\s+de\s+\d{4})', asset)
        
        if title_match:
            pub = {
                'titulo': title_match.group(1).strip(),
                'fecha': date_match.group(1).strip() if date_match else '',
                'tipo': 'Publicación'
            }
            if not any(p.get('titulo') == pub['titulo'] for p in publicaciones):
                publicaciones.append(pub)
    
    # Asociar documentos con publicaciones si es posible
    for pub in publicaciones:
        pub['documentos'] = []
        for doc in documentos:
            # Si el documento menciona el mismo número de estado
            if pub.get('numero') and pub['numero'] in doc['nombre']:
                pub['documentos'].append(doc)
    
    return {
        'publicaciones': publicaciones,
        'documentos': documentos
    }


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Si es una petición al proxy de Rama Judicial (consulta procesos)
        if self.path.startswith('/api/rama/'):
            self.proxy_rama_judicial()
        # Si es una petición a publicaciones procesales
        elif self.path.startswith('/api/publicaciones'):
            self.proxy_publicaciones()
        # Si es una petición para descargar un documento
        elif self.path.startswith('/api/documento'):
            self.proxy_documento()
        else:
            # Servir archivos estáticos normalmente
            super().do_GET()
    
    def proxy_rama_judicial(self):
        """Proxy para la API de consulta de procesos"""
        rama_path = self.path.replace('/api/rama/', '')
        rama_url = f'https://consultaprocesos.ramajudicial.gov.co:448/api/v2/{rama_path}'
        
        print(f'[Consulta Procesos] Proxy request: {rama_url}')
        
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            req = urllib.request.Request(rama_url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            req.add_header('Accept', 'application/json')
            
            with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                data = response.read()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', '*')
                self.end_headers()
                self.wfile.write(data)
                
        except urllib.error.HTTPError as e:
            print(f'HTTP Error: {e.code} - {e.reason}')
            self.send_error(e.code, e.reason)
        except Exception as e:
            print(f'Error: {str(e)}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
    
    def proxy_publicaciones(self):
        """Proxy para la API de publicaciones procesales"""
        global cookie_jar
        
        # Parsear query string
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        
        # Extraer parámetros
        fecha_inicio = params.get('fechaInicio', [''])[0]
        fecha_fin = params.get('fechaFin', [''])[0]
        id_depto = params.get('idDepto', ['08'])[0]
        id_muni = params.get('idMuni', ['08001'])[0]
        id_despacho = params.get('idDespacho', [''])[0]
        id_depto_category = params.get('idDeptoIdCategory', ['178847290'])[0]
        
        # Namespace del portlet
        ns = '_co_com_avanti_efectosProcesales_PublicacionesEfectosProcesalesPortletV2_INSTANCE_BIyXQFHVaYaq_'
        
        # Construir URL completa
        base_url = 'https://publicacionesprocesales.ramajudicial.gov.co/web/publicaciones-procesales/inicio'
        
        query_params = {
            'p_p_id': 'co_com_avanti_efectosProcesales_PublicacionesEfectosProcesalesPortletV2_INSTANCE_BIyXQFHVaYaq',
            'p_p_lifecycle': '0',
            'p_p_state': 'normal',
            'p_p_mode': 'view',
            f'{ns}action': 'busqueda',
            f'{ns}fechaInicio': fecha_inicio,
            f'{ns}fechaFin': fecha_fin,
            f'{ns}idDepto': id_depto,
            f'{ns}idMuni': id_muni,
            f'{ns}idDespacho': id_despacho,
            f'{ns}verTotales': 'true',
            f'{ns}idDeptoIdCategory': id_depto_category
        }
        
        full_url = base_url + '?' + urllib.parse.urlencode(query_params)
        
        print(f'[Publicaciones] Request: {full_url[:100]}...')
        
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            # Crear opener con cookie jar
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(cookie_jar),
                urllib.request.HTTPSHandler(context=ctx)
            )
            
            # Primero, obtener sesión si no tenemos cookies
            if len(cookie_jar) == 0:
                print('[Publicaciones] Obteniendo sesión...')
                init_req = urllib.request.Request(base_url)
                init_req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
                init_req.add_header('Accept', 'text/html,application/xhtml+xml')
                opener.open(init_req, timeout=15)
                print(f'[Publicaciones] Cookies obtenidas: {len(cookie_jar)}')
            
            # Hacer la petición real
            req = urllib.request.Request(full_url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Language', 'es-CO,es;q=0.9,en;q=0.8')
            req.add_header('Referer', base_url)
            
            with opener.open(req, timeout=15) as response:
                html_data = response.read().decode('utf-8', errors='ignore')
                
                print(f'[Publicaciones] HTML recibido: {len(html_data)} bytes')
                
                # Parsear el HTML para extraer publicaciones y documentos
                parsed = parse_publicaciones_html(html_data)
                publicaciones = parsed['publicaciones']
                documentos = parsed['documentos']
                
                print(f'[Publicaciones] Publicaciones encontradas: {len(publicaciones)}')
                print(f'[Publicaciones] Documentos encontrados: {len(documentos)}')
                
                # Devolver JSON con los resultados
                result = {
                    'success': True,
                    'total': len(publicaciones),
                    'totalDocumentos': len(documentos),
                    'publicaciones': publicaciones,
                    'documentos': documentos,
                    'parametros': {
                        'fechaInicio': fecha_inicio,
                        'fechaFin': fecha_fin,
                        'idDespacho': id_despacho
                    },
                    'html_size': len(html_data),
                    'html_preview': html_data[:1000] if len(documentos) == 0 else None
                }
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))
                
        except urllib.error.HTTPError as e:
            print(f'[Publicaciones] HTTP Error: {e.code} - {e.reason}')
            error_body = e.read().decode('utf-8', errors='ignore') if e.fp else ''
            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': False, 
                'error': f'{e.code}: {e.reason}',
                'detail': error_body[:500]
            }).encode())
        except Exception as e:
            print(f'[Publicaciones] Error: {str(e)}')
            import traceback
            traceback.print_exc()
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': False, 
                'error': str(e)
            }).encode())
    
    def do_OPTIONS(self):
        # Manejar preflight CORS
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()
    
    def proxy_documento(self):
        """Proxy para descargar documentos PDF de publicaciones procesales"""
        global cookie_jar
        
        # Parsear query string para obtener la URL del documento
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        
        doc_url = params.get('url', [''])[0]
        
        if not doc_url:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'URL del documento no proporcionada'}).encode())
            return
        
        # Decodificar la URL si está encoded
        doc_url = urllib.parse.unquote(doc_url)
        
        # Asegurar que sea una URL completa
        if doc_url.startswith('/'):
            doc_url = DOCS_BASE_URL + doc_url
        
        print(f'[Documento] Descargando: {doc_url[:80]}...')
        
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            # Crear opener con cookie jar
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(cookie_jar),
                urllib.request.HTTPSHandler(context=ctx)
            )
            
            # Obtener sesión si no tenemos cookies
            if len(cookie_jar) == 0:
                print('[Documento] Obteniendo sesión...')
                init_url = 'https://publicacionesprocesales.ramajudicial.gov.co/web/publicaciones-procesales/inicio'
                init_req = urllib.request.Request(init_url)
                init_req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
                opener.open(init_req, timeout=15)
            
            # Descargar el documento
            req = urllib.request.Request(doc_url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            req.add_header('Accept', 'application/pdf,*/*')
            req.add_header('Referer', 'https://publicacionesprocesales.ramajudicial.gov.co/')
            
            with opener.open(req, timeout=60) as response:
                # Leer el contenido del PDF
                pdf_data = response.read()
                content_type = response.headers.get('Content-Type', 'application/pdf')
                
                # Extraer nombre del archivo de la URL
                nombre_match = re.search(r'/([^/]+\.pdf)', doc_url, re.IGNORECASE)
                if nombre_match:
                    filename = urllib.parse.unquote(nombre_match.group(1)).replace('+', ' ')
                else:
                    filename = 'documento.pdf'
                
                print(f'[Documento] Descargado: {len(pdf_data)} bytes - {filename}')
                
                # Enviar el PDF
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(pdf_data)))
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', '*')
                self.send_header('Access-Control-Expose-Headers', 'Content-Disposition')
                self.end_headers()
                self.wfile.write(pdf_data)
                
        except urllib.error.HTTPError as e:
            print(f'[Documento] HTTP Error: {e.code} - {e.reason}')
            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'error': f'Error al descargar documento: {e.code} {e.reason}'
            }).encode())
        except Exception as e:
            print(f'[Documento] Error: {str(e)}')
            import traceback
            traceback.print_exc()
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'error': f'Error al descargar documento: {str(e)}'
            }).encode())

if __name__ == '__main__':
    # Permitir reutilizar el puerto
    socketserver.TCPServer.allow_reuse_address = True
    
    with socketserver.TCPServer(("", PORT), ProxyHandler) as httpd:
        print(f"")
        print(f"╔═══════════════════════════════════════════════════════════╗")
        print(f"║     SERVIDOR PROXY CORS - Rama Judicial Colombia         ║")
        print(f"╠═══════════════════════════════════════════════════════════╣")
        print(f"║                                                           ║")
        print(f"║  Abrir en navegador: http://localhost:{PORT}               ║")
        print(f"║                                                           ║")
        print(f"║  Endpoints disponibles:                                   ║")
        print(f"║    /api/rama/...         - Consulta de procesos           ║")
        print(f"║    /api/publicaciones/   - Publicaciones procesales       ║")
        print(f"║    /api/documento?url=   - Descarga de PDFs               ║")
        print(f"║                                                           ║")
        print(f"╚═══════════════════════════════════════════════════════════╝")
        print(f"")
        print(f"Presiona Ctrl+C para detener el servidor")
        print(f"")
        httpd.serve_forever()
