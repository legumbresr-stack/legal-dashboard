#!/usr/bin/env python3
"""
Servidor proxy local para evitar CORS con las APIs de Rama Judicial.
Ejecutar: python proxy_server.py
Luego abrir: http://localhost:8000

Endpoints disponibles:
  /api/rama/...           -> API de consulta de procesos
  /api/publicaciones/...  -> API de publicaciones procesales
"""

import http.server
import socketserver
import urllib.request
import urllib.error
import urllib.parse
import json
import ssl
import re
from html.parser import HTMLParser
import http.cookiejar

PORT = 8000

# Cookie jar global para mantener sesión con publicaciones procesales
cookie_jar = http.cookiejar.CookieJar()

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
    """Parsear HTML y extraer publicaciones como JSON"""
    publicaciones = []
    
    # Buscar patrones comunes de publicaciones en el HTML
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
    
    # Patrón 2: Fecha de Publicación
    pattern_fecha = r'Fecha de Publicación[:\s]*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})'
    fechas = re.findall(pattern_fecha, html_content, re.IGNORECASE)
    
    # Patrón 3: Auto interlocutorio
    pattern_auto = r'Auto\s+(interlocutorio|de\s+sustanciación)[^<]*'
    autos = re.findall(pattern_auto, html_content, re.IGNORECASE)
    
    # Patrón 4: Asset entries más genérico
    pattern_asset = r'<div[^>]*class="[^"]*asset-abstract[^"]*"[^>]*>(.*?)</div>'
    assets = re.findall(pattern_asset, html_content, re.IGNORECASE | re.DOTALL)
    
    for asset in assets:
        # Extraer título
        title_match = re.search(r'<a[^>]*>([^<]+)</a>', asset)
        # Extraer fecha
        date_match = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d+\s+de\s+\w+\s+de\s+\d{4})', asset)
        
        if title_match:
            pub = {
                'titulo': title_match.group(1).strip(),
                'fecha': date_match.group(1).strip() if date_match else '',
                'tipo': 'Publicación',
                'raw_html': asset[:200]  # Primeros 200 chars para debug
            }
            # Evitar duplicados
            if not any(p.get('titulo') == pub['titulo'] for p in publicaciones):
                publicaciones.append(pub)
    
    # Patrón 5: Buscar en tablas
    pattern_table_row = r'<tr[^>]*>(.*?)</tr>'
    rows = re.findall(pattern_table_row, html_content, re.IGNORECASE | re.DOTALL)
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        if len(cells) >= 2:
            # Limpiar HTML de las celdas
            clean_cells = [re.sub(r'<[^>]+>', '', cell).strip() for cell in cells]
            if any(clean_cells) and not all(c == '' for c in clean_cells):
                pub = {
                    'titulo': clean_cells[0] if clean_cells else '',
                    'fecha': clean_cells[1] if len(clean_cells) > 1 else '',
                    'detalles': ' | '.join(clean_cells[2:]) if len(clean_cells) > 2 else '',
                    'tipo': 'Tabla'
                }
                if pub['titulo'] and pub['titulo'] not in ['', 'Título', 'Fecha', 'Acciones']:
                    if not any(p.get('titulo') == pub['titulo'] for p in publicaciones):
                        publicaciones.append(pub)
    
    # Si no encontramos nada estructurado, buscar texto relevante
    if not publicaciones:
        # Buscar cualquier mención de fechas con contexto
        pattern_context = r'([^.]{0,100}(?:notificación|estado|auto|sentencia|edicto)[^.]{0,100})'
        contexts = re.findall(pattern_context, html_content, re.IGNORECASE)
        for ctx in contexts[:10]:  # Máximo 10
            clean_ctx = re.sub(r'<[^>]+>', '', ctx).strip()
            if clean_ctx and len(clean_ctx) > 20:
                publicaciones.append({
                    'titulo': clean_ctx[:150],
                    'fecha': '',
                    'tipo': 'Extracto'
                })
    
    return publicaciones


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Si es una petición al proxy de Rama Judicial (consulta procesos)
        if self.path.startswith('/api/rama/'):
            self.proxy_rama_judicial()
        # Si es una petición a publicaciones procesales
        elif self.path.startswith('/api/publicaciones'):
            self.proxy_publicaciones()
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
            
            with opener.open(req, timeout=30) as response:
                html_data = response.read().decode('utf-8', errors='ignore')
                
                print(f'[Publicaciones] HTML recibido: {len(html_data)} bytes')
                
                # Parsear el HTML para extraer publicaciones
                publicaciones = parse_publicaciones_html(html_data)
                
                print(f'[Publicaciones] Publicaciones encontradas: {len(publicaciones)}')
                
                # Devolver JSON con los resultados
                result = {
                    'success': True,
                    'total': len(publicaciones),
                    'publicaciones': publicaciones,
                    'parametros': {
                        'fechaInicio': fecha_inicio,
                        'fechaFin': fecha_fin,
                        'idDespacho': id_despacho
                    },
                    'html_size': len(html_data),
                    'html_preview': html_data[:500] if len(publicaciones) == 0 else None  # Preview solo si no hay resultados
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
        print(f"║                                                           ║")
        print(f"╚═══════════════════════════════════════════════════════════╝")
        print(f"")
        print(f"Presiona Ctrl+C para detener el servidor")
        print(f"")
        httpd.serve_forever()
