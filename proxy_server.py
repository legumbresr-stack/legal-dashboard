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


def extraer_urls_estados(html_content):
    """Extraer URLs de las páginas de detalle de cada Estado (VER DETALLE)"""
    urls = []
    
    # El botón "VER DETALLE" tiene URLs con este patrón:
    # jspPage=%2FMETA-INF%2Fresources%2Fdetail.jsp&...&articleId=NUMERO
    # o: jspPage=/META-INF/resources/detail.jsp&...&articleId=NUMERO
    
    # Patrón 1: Buscar URLs con detail.jsp y articleId
    pattern_detail = r'href=["\']([^"\']*detail\.jsp[^"\']*articleId=\d+[^"\']*)["\']'
    matches = re.findall(pattern_detail, html_content, re.IGNORECASE)
    
    for url in matches:
        # Decodificar URL si está encoded
        url_decoded = urllib.parse.unquote(url)
        
        if url_decoded.startswith('/'):
            full_url = DOCS_BASE_URL + url_decoded
        elif url_decoded.startswith('http'):
            full_url = url_decoded
        else:
            full_url = DOCS_BASE_URL + '/' + url_decoded
        
        if full_url not in urls:
            urls.append(full_url)
            print(f'[Estados] URL de detalle encontrada: {full_url[:100]}...')
    
    # Patrón 2: Buscar articleId en cualquier enlace del portlet
    pattern_article = r'href=["\']([^"\']*articleId=(\d+)[^"\']*)["\']'
    article_matches = re.findall(pattern_article, html_content, re.IGNORECASE)
    
    for url, article_id in article_matches:
        url_decoded = urllib.parse.unquote(url)
        
        # Solo si parece ser una URL de detalle (tiene el portlet ID)
        if 'PublicacionesEfectosProcesales' in url_decoded or 'detail' in url_decoded.lower():
            if url_decoded.startswith('/'):
                full_url = DOCS_BASE_URL + url_decoded
            elif url_decoded.startswith('http'):
                full_url = url_decoded
            else:
                full_url = DOCS_BASE_URL + '/web/publicaciones-procesales/inicio?' + url_decoded
            
            if full_url not in urls:
                urls.append(full_url)
                print(f'[Estados] URL de detalle (articleId={article_id}): {full_url[:80]}...')
    
    # Patrón 3: Buscar enlaces que contengan "VER DETALLE" o similar
    pattern_ver_detalle = r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>[^<]*(?:VER DETALLE|Ver Detalle|ver detalle)[^<]*</a>'
    ver_matches = re.findall(pattern_ver_detalle, html_content, re.IGNORECASE)
    
    for url in ver_matches:
        url_decoded = urllib.parse.unquote(url)
        
        if url_decoded.startswith('/'):
            full_url = DOCS_BASE_URL + url_decoded
        elif url_decoded.startswith('http'):
            full_url = url_decoded
        else:
            full_url = DOCS_BASE_URL + '/' + url_decoded
        
        if full_url not in urls:
            urls.append(full_url)
            print(f'[Estados] URL de VER DETALLE: {full_url[:80]}...')
    
    print(f'[Estados] Total URLs de detalle encontradas: {len(urls)}')
    return urls


def parse_detalle_estado(html_content, estado_titulo='', opener=None, base_url=''):
    """Parsear la página de detalle de un Estado para extraer documentos de expedientes.
    Si hay paginación, intenta obtener documentos de todas las páginas."""
    documentos = []
    
    print(f'[Detalle Estado] Parseando página de detalle ({len(html_content)} bytes)...')
    
    # ===== DETECTAR PAGINACIÓN =====
    # Buscar "Mostrando el intervalo X - Y de Z resultados" o enlaces de paginación
    total_docs_match = re.search(r'de\s+(\d+)\s+resultados', html_content, re.IGNORECASE)
    total_documentos = int(total_docs_match.group(1)) if total_docs_match else 0
    
    if total_documentos > 0:
        print(f'[Detalle Estado] Total documentos en estado: {total_documentos}')
    
    # ===== BUSCAR EN LA TABLA "Documentos de la publicación" =====
    # La tabla tiene columnas: Nombre del Documento | Fecha Incorporación
    # Los documentos tienen formato: 202100169 - DG.pdf, 2021-00169 - DG.pdf, etc.
    
    # Buscar todos los enlaces a PDFs en la página
    # Patrón: <a href="...">XXXXXXXXX - XX.pdf</a> o similar
    pattern_pdf_links = r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>\s*([^<]*\.pdf)\s*</a>'
    all_pdf_matches = re.findall(pattern_pdf_links, html_content, re.IGNORECASE)
    
    print(f'[Detalle Estado] PDFs encontrados en página: {len(all_pdf_matches)}')
    
    for url, nombre in all_pdf_matches:
        nombre_limpio = nombre.strip()
        
        # Verificar si es un documento de expediente (empieza con dígitos)
        # Formatos: 202100169 - DG.pdf, 2021-00169 - DG.pdf, 202100169.pdf
        codigo_expediente = ''
        
        # Patrón 1: 9 dígitos seguidos al inicio (202100169)
        match_9dig = re.match(r'^(\d{9})', nombre_limpio)
        if match_9dig:
            codigo_expediente = match_9dig.group(1)
        
        # Patrón 2: Formato con guión (2021-00169)
        if not codigo_expediente:
            match_guion = re.match(r'^(\d{4})[-_](\d{5})', nombre_limpio)
            if match_guion:
                codigo_expediente = match_guion.group(1) + match_guion.group(2)
        
        # Patrón 3: Cualquier secuencia de dígitos al inicio que parezca código
        if not codigo_expediente:
            match_numeros = re.match(r'^(\d{7,9})', nombre_limpio)
            if match_numeros:
                codigo_expediente = match_numeros.group(1).zfill(9)  # Rellenar a 9 dígitos
        
        # Solo procesar si encontramos un código de expediente
        if codigo_expediente:
            if url.startswith('/'):
                full_url = DOCS_BASE_URL + url
            elif url.startswith('http'):
                full_url = url
            else:
                full_url = DOCS_BASE_URL + '/' + url
            
            # Buscar fecha cerca del documento
            fecha = ''
            # Buscar patrón de fecha en el HTML cercano: dd-mmm-yyyy o similar
            fecha_pattern = rf'{re.escape(nombre_limpio)}.*?(\d{{1,2}}[-/]\w{{3}}[-/]\d{{4}}(?:\s+\d{{1,2}}:\d{{2}}(?::\d{{2}})?)?)'
            fecha_match = re.search(fecha_pattern, html_content, re.IGNORECASE | re.DOTALL)
            if fecha_match:
                fecha = fecha_match.group(1)
            
            doc = {
                'nombre': nombre_limpio,
                'url': full_url,
                'fecha': fecha,
                'tipo': 'DocumentoExpediente',
                'codigoExpediente': codigo_expediente,
                'estadoOrigen': estado_titulo,
                'esDocumentoExpediente': True
            }
            
            if not any(d.get('url') == doc['url'] for d in documentos):
                documentos.append(doc)
                print(f'[Detalle Estado] ✓ Documento de expediente: {nombre_limpio} (código: {codigo_expediente})')
    
    print(f'[Detalle Estado] Total documentos de expediente encontrados: {len(documentos)}')
    return documentos


def parse_publicaciones_html(html_content):
    """Parsear HTML y extraer publicaciones con sus documentos PDF"""
    publicaciones = []
    documentos = []
    urls_detalle = []
    
    # ===== EXTRAER URLs DE DETALLE DE ESTADOS =====
    urls_detalle = extraer_urls_estados(html_content)
    
    # DEBUG: Guardar HTML para análisis si no encuentra URLs
    if len(urls_detalle) == 0:
        # Buscar si hay botones o enlaces con texto "VER DETALLE" o "Ver Detalle"
        if 'VER DETALLE' in html_content or 'Ver Detalle' in html_content or 'ver detalle' in html_content.lower():
            print('[DEBUG] El HTML contiene "VER DETALLE" pero no se encontraron URLs')
            # Buscar el contexto alrededor de "VER DETALLE"
            idx = html_content.lower().find('ver detalle')
            if idx > 0:
                contexto = html_content[max(0, idx-500):idx+500]
                print(f'[DEBUG] Contexto alrededor de VER DETALLE:\n{contexto[:1000]}')
        else:
            print('[DEBUG] El HTML NO contiene "VER DETALLE"')
    
    # ===== EXTRAER DOCUMENTOS PDF DIRECTOS (Estados principales) =====
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
        
        # Verificar si es documento de expediente (9 dígitos al inicio)
        es_expediente = bool(re.match(r'^\d{9}', nombre))
        codigo_expediente = ''
        if es_expediente:
            codigo_match = re.match(r'(\d{9})', nombre)
            codigo_expediente = codigo_match.group(1) if codigo_match else ''
        
        doc = {
            'nombre': nombre,
            'url': full_url,
            'fecha': fecha,
            'tipo': 'DocumentoExpediente' if es_expediente else 'PDF',
            'esDocumentoExpediente': es_expediente,
            'codigoExpediente': codigo_expediente
        }
        
        # Evitar duplicados
        if not any(d.get('url') == doc['url'] for d in documentos):
            documentos.append(doc)
    
    # ===== EXTRAER DE TABLAS DE DOCUMENTOS =====
    # Patrón más específico para tablas con documentos del tipo "202500338 - DG.pdf"
    # Buscar todos los enlaces que contienen .pdf con su texto
    pattern_link_pdf = r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*\.pdf)</a>'
    link_matches = re.findall(pattern_link_pdf, html_content, re.IGNORECASE)
    
    for url, nombre in link_matches:
        if url.startswith('/'):
            full_url = DOCS_BASE_URL + url
        else:
            full_url = url
            
        nombre_limpio = nombre.strip()
        
        # Buscar fecha en el contexto cercano (siguiente columna de tabla)
        fecha = ''
        # Buscar patrón de fecha cerca del enlace: dd-mmm-yyyy hh:mm:ss
        fecha_pattern = rf'{re.escape(nombre)}.*?(\d{{2}}-\w{{3}}-\d{{4}}\s+\d{{1,2}}:\d{{2}}:\d{{2}})'
        fecha_match = re.search(fecha_pattern, html_content, re.IGNORECASE | re.DOTALL)
        if fecha_match:
            fecha = fecha_match.group(1)
        
        # Verificar si es documento de expediente
        es_expediente = bool(re.match(r'^\d{9}\s*-', nombre_limpio))
        codigo_expediente = ''
        if es_expediente:
            codigo_match = re.match(r'(\d{9})', nombre_limpio)
            codigo_expediente = codigo_match.group(1) if codigo_match else ''
        
        doc = {
            'nombre': nombre_limpio,
            'url': full_url,
            'fecha': fecha,
            'tipo': 'DocumentoExpediente' if es_expediente else 'PDF',
            'esDocumentoExpediente': es_expediente,
            'codigoExpediente': codigo_expediente
        }
        
        if not any(d.get('url') == doc['url'] for d in documentos):
            documentos.append(doc)
            print(f'[Parser] Documento encontrado: {nombre_limpio}')
    
    # ===== BUSCAR ESPECÍFICAMENTE DOCUMENTOS DE EXPEDIENTE (XXXXXXXXX - XX.pdf) =====
    # Patrón para documentos tipo "202500338 - DG.pdf" o "202300036 - DG.pdf"
    pattern_expediente = r'(\d{9})\s*-\s*\w+\.pdf'
    expediente_matches = re.findall(pattern_expediente, html_content, re.IGNORECASE)
    print(f'[Parser] Códigos de expediente encontrados en búsqueda inicial: {expediente_matches}')
    
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
    
    print(f'[Parser] Total: {len(publicaciones)} publicaciones, {len(documentos)} documentos, {len(urls_detalle)} URLs de detalle')
    
    return {
        'publicaciones': publicaciones,
        'documentos': documentos,
        'urls_detalle': urls_detalle
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
        """Proxy para la API de publicaciones procesales con consulta profunda"""
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
        consulta_profunda = params.get('profunda', ['true'])[0].lower() == 'true'
        
        print(f'[Publicaciones] Parámetro profunda recibido: {params.get("profunda", ["NO ENVIADO"])}')
        print(f'[Publicaciones] Consulta profunda activada: {consulta_profunda}')
        
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
        print(f'[Publicaciones] Consulta profunda: {consulta_profunda}')
        
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
                opener.open(init_req, timeout=30)
                print(f'[Publicaciones] Cookies obtenidas: {len(cookie_jar)}')
            
            # Hacer la petición inicial (búsqueda)
            req = urllib.request.Request(full_url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Language', 'es-CO,es;q=0.9,en;q=0.8')
            req.add_header('Referer', base_url)
            
            with opener.open(req, timeout=45) as response:
                html_data = response.read().decode('utf-8', errors='ignore')
                
                print(f'[Publicaciones] HTML recibido: {len(html_data)} bytes')
                
                # Parsear el HTML para extraer publicaciones, documentos y URLs de detalle
                parsed_result = parse_publicaciones_html(html_data)
                publicaciones = parsed_result['publicaciones']
                documentos = parsed_result['documentos']
                urls_detalle = parsed_result.get('urls_detalle', [])
                
                print(f'[Publicaciones] Búsqueda inicial (página 1): {len(publicaciones)} publicaciones, {len(documentos)} documentos')
                print(f'[Publicaciones] URLs de detalle encontradas: {len(urls_detalle)}')
                
                # ===== PAGINACIÓN DE BÚSQUEDA: Obtener más estados de páginas adicionales =====
                # Buscar si hay más páginas de resultados
                total_estados_match = re.search(r'de\s+(\d+)\s+resultados', html_data, re.IGNORECASE)
                if total_estados_match:
                    total_estados = int(total_estados_match.group(1))
                    estados_por_pagina = 10
                    paginas_totales = (total_estados + estados_por_pagina - 1) // estados_por_pagina
                    
                    print(f'[Publicaciones] ★★★ Total estados encontrados: {total_estados} en {paginas_totales} páginas ★★★')
                    
                    if paginas_totales > 1:
                        print(f'[Publicaciones] Detectada paginación en búsqueda: {total_estados} estados en {paginas_totales} páginas')
                        
                        # MÉTODO MEJORADO: Buscar TODOS los enlaces de paginación en el HTML
                        # Los portales Liferay usan diferentes patrones:
                        # 1. {namespace}cur=X  (página X)
                        # 2. delta=X (offset)
                        # 3. Enlaces numéricos directos
                        
                        # Buscar todos los enlaces de paginación disponibles
                        pag_patterns = [
                            # Patrón con namespace del portlet
                            rf'href=["\']([^"\']*{re.escape(ns)}cur=(\d+)[^"\']*)["\']',
                            # Patrón genérico con cur=
                            r'href=["\']([^"\']*[?&]cur=(\d+)[^"\']*)["\']',
                            # Patrón con delta (offset)
                            r'href=["\']([^"\']*[?&]delta=(\d+)[^"\']*)["\']',
                            # Patrón de SearchContainer (suele tener estos parámetros)
                            r'href=["\']([^"\']*SearchContainer[^"\']*cur=(\d+)[^"\']*)["\']',
                        ]
                        
                        paginas_encontradas = {}
                        for pattern in pag_patterns:
                            matches = re.findall(pattern, html_data, re.IGNORECASE)
                            for url, num in matches:
                                num_int = int(num)
                                if num_int > 1 and num_int not in paginas_encontradas:
                                    paginas_encontradas[num_int] = url
                                    print(f'[Publicaciones] Enlace página {num_int} encontrado')
                        
                        print(f'[Publicaciones] Páginas de paginación detectadas: {list(paginas_encontradas.keys())}')
                        
                        # Si encontramos enlaces directos, usarlos
                        if paginas_encontradas:
                            for pagina in sorted(paginas_encontradas.keys())[:19]:  # Máximo 19 páginas adicionales
                                try:
                                    pag_url = paginas_encontradas[pagina]
                                    
                                    # Decodificar entidades HTML
                                    pag_url = pag_url.replace('&amp;', '&')
                                    
                                    if pag_url.startswith('/'):
                                        pag_url = DOCS_BASE_URL + pag_url
                                    elif not pag_url.startswith('http'):
                                        pag_url = DOCS_BASE_URL + '/web/publicaciones-procesales/inicio?' + pag_url
                                    
                                    print(f'[Publicaciones] Consultando página {pagina} de estados: {pag_url[:100]}...')
                                    req_pag = urllib.request.Request(pag_url)
                                    req_pag.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
                                    req_pag.add_header('Accept', 'text/html')
                                    req_pag.add_header('Referer', full_url)
                                    
                                    with opener.open(req_pag, timeout=45) as resp_pag:
                                        html_pag = resp_pag.read().decode('utf-8', errors='ignore')
                                        parsed_pag = parse_publicaciones_html(html_pag)
                                        
                                        nuevas_urls = 0
                                        for url in parsed_pag.get('urls_detalle', []):
                                            if url not in urls_detalle:
                                                urls_detalle.append(url)
                                                nuevas_urls += 1
                                        
                                        print(f'[Publicaciones] Página {pagina}: {nuevas_urls} estados nuevos (total: {len(urls_detalle)})')
                                        
                                        # Buscar más páginas en esta respuesta
                                        for pattern in pag_patterns:
                                            matches = re.findall(pattern, html_pag, re.IGNORECASE)
                                            for url, num in matches:
                                                num_int = int(num)
                                                if num_int > pagina and num_int not in paginas_encontradas:
                                                    paginas_encontradas[num_int] = url
                                                    
                                except Exception as e:
                                    print(f'[Publicaciones] Error en página {pagina} de estados: {str(e)[:50]}')
                                    continue
                        else:
                            # MÉTODO ALTERNATIVO: Construir URLs de paginación manualmente
                            print(f'[Publicaciones] No se encontraron enlaces de paginación directos, intentando construcción manual...')
                            
                            for pagina in range(2, min(paginas_totales + 1, 20)):
                                try:
                                    # Agregar parámetro cur= a la URL base
                                    pag_params = query_params.copy()
                                    pag_params[f'{ns}cur'] = str(pagina)
                                    
                                    pag_url = base_url + '?' + urllib.parse.urlencode(pag_params)
                                    
                                    print(f'[Publicaciones] Intentando página {pagina} (construcción manual)...')
                                    req_pag = urllib.request.Request(pag_url)
                                    req_pag.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
                                    req_pag.add_header('Accept', 'text/html')
                                    req_pag.add_header('Referer', full_url)
                                    
                                    with opener.open(req_pag, timeout=45) as resp_pag:
                                        html_pag = resp_pag.read().decode('utf-8', errors='ignore')
                                        parsed_pag = parse_publicaciones_html(html_pag)
                                        
                                        nuevas_urls = 0
                                        for url in parsed_pag.get('urls_detalle', []):
                                            if url not in urls_detalle:
                                                urls_detalle.append(url)
                                                nuevas_urls += 1
                                        
                                        if nuevas_urls == 0:
                                            print(f'[Publicaciones] Página {pagina}: sin nuevos estados, deteniendo paginación')
                                            break
                                            
                                        print(f'[Publicaciones] Página {pagina}: {nuevas_urls} estados nuevos (total: {len(urls_detalle)})')
                                        
                                except Exception as e:
                                    print(f'[Publicaciones] Error en página {pagina}: {str(e)[:50]}')
                                    break
                        
                        print(f'[Publicaciones] ★★★ Total URLs de detalle después de paginación: {len(urls_detalle)} ★★★')
                else:
                    print(f'[Publicaciones] ⚠️ No se detectó patrón "de X resultados" en el HTML')
                
                # ===== CONSULTA PROFUNDA: entrar a cada Estado =====
                documentos_expediente = []
                estados_consultados = 0
                
                if consulta_profunda and urls_detalle:
                    # Obtener límite de estados desde parámetro (default: 50, máximo: 200)
                    max_estados = min(int(params.get('maxEstados', ['50'])[0]), 200)
                    urls_a_consultar = urls_detalle[:max_estados]
                    print(f'[Publicaciones] Iniciando consulta profunda de {len(urls_a_consultar)} estados (de {len(urls_detalle)} encontrados, máx configurado: {max_estados})...')
                    
                    for url_estado in urls_a_consultar:
                        try:
                            # Asegurar que la URL esté correctamente codificada
                            # Parsear y re-codificar la URL para manejar caracteres especiales
                            try:
                                parsed_url = urllib.parse.urlparse(url_estado)
                                # Re-codificar el query string
                                if parsed_url.query:
                                    query_params_estado = urllib.parse.parse_qs(parsed_url.query, keep_blank_values=True)
                                    query_encoded = urllib.parse.urlencode(query_params_estado, doseq=True)
                                    url_estado_clean = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{query_encoded}"
                                else:
                                    url_estado_clean = url_estado
                            except:
                                url_estado_clean = url_estado
                            
                            print(f'[Publicaciones] Consultando estado: {url_estado_clean[:80]}...')
                            
                            req_detalle = urllib.request.Request(url_estado_clean)
                            req_detalle.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
                            req_detalle.add_header('Accept', 'text/html,application/xhtml+xml')
                            req_detalle.add_header('Accept-Language', 'es-CO,es;q=0.9')
                            req_detalle.add_header('Referer', full_url)
                            
                            with opener.open(req_detalle, timeout=30) as resp_detalle:
                                html_detalle = resp_detalle.read().decode('utf-8', errors='ignore')
                                
                                # Extraer título del estado de la URL o del HTML
                                titulo_estado = ''
                                titulo_match = re.search(r'<title>([^<]+)</title>', html_detalle, re.IGNORECASE)
                                if titulo_match:
                                    titulo_estado = titulo_match.group(1).strip()
                                
                                # Parsear documentos de expediente del detalle (primera página)
                                docs_estado = parse_detalle_estado(html_detalle, titulo_estado)
                                
                                # ===== PAGINACIÓN: Buscar y consultar páginas adicionales =====
                                # Buscar total de resultados
                                total_match = re.search(r'de\s+(\d+)\s+resultados', html_detalle, re.IGNORECASE)
                                if total_match:
                                    total_docs = int(total_match.group(1))
                                    docs_por_pagina = 10  # Default de la Rama Judicial
                                    paginas_totales = (total_docs + docs_por_pagina - 1) // docs_por_pagina
                                    
                                    if paginas_totales > 1:
                                        print(f'[Detalle Estado] Detectada paginación: {total_docs} documentos en {paginas_totales} páginas')
                                        
                                        # Buscar enlaces de paginación (páginas 2, 3, etc.)
                                        # Los enlaces suelen tener delta=X donde X es el offset
                                        for pagina in range(2, min(paginas_totales + 1, 11)):  # Máximo 10 páginas
                                            offset = (pagina - 1) * docs_por_pagina
                                            
                                            # Buscar el enlace de la página en el HTML
                                            # Patrón: href="...&delta=X..." o href="...cur=X..."
                                            pag_pattern = rf'href=["\']([^"\']*(?:delta={offset}|cur={pagina}|page={pagina})[^"\']*)["\']'
                                            pag_match = re.search(pag_pattern, html_detalle, re.IGNORECASE)
                                            
                                            if pag_match:
                                                pag_url = pag_match.group(1)
                                                if pag_url.startswith('/'):
                                                    pag_url = DOCS_BASE_URL + pag_url
                                                elif not pag_url.startswith('http'):
                                                    pag_url = DOCS_BASE_URL + '/' + pag_url
                                                
                                                try:
                                                    print(f'[Detalle Estado] Consultando página {pagina} de documentos...')
                                                    req_pag = urllib.request.Request(pag_url)
                                                    req_pag.add_header('User-Agent', 'Mozilla/5.0')
                                                    req_pag.add_header('Accept', 'text/html')
                                                    req_pag.add_header('Referer', url_estado_clean)
                                                    
                                                    with opener.open(req_pag, timeout=30) as resp_pag:
                                                        html_pag = resp_pag.read().decode('utf-8', errors='ignore')
                                                        docs_pag = parse_detalle_estado(html_pag, titulo_estado)
                                                        
                                                        for doc in docs_pag:
                                                            if not any(d.get('url') == doc['url'] for d in docs_estado):
                                                                docs_estado.append(doc)
                                                        
                                                        print(f'[Detalle Estado] Página {pagina}: {len(docs_pag)} documentos adicionales')
                                                except Exception as e:
                                                    print(f'[Detalle Estado] Error en página {pagina}: {str(e)[:30]}')
                                
                                for doc in docs_estado:
                                    if not any(d.get('url') == doc['url'] for d in documentos_expediente):
                                        documentos_expediente.append(doc)
                                
                                estados_consultados += 1
                                print(f'[Publicaciones] Estado consultado: {len(docs_estado)} documentos de expediente encontrados')
                                
                        except Exception as e:
                            print(f'[Publicaciones] Error consultando estado (timeout/red): {str(e)[:50]}')
                            continue
                    
                    print(f'[Publicaciones] Consulta profunda completa: {estados_consultados} estados, {len(documentos_expediente)} documentos de expediente')
                
                # Combinar documentos
                todos_documentos = documentos.copy()
                for doc in documentos_expediente:
                    if not any(d.get('url') == doc['url'] for d in todos_documentos):
                        todos_documentos.append(doc)
                
                # Separar documentos de expediente
                docs_expediente_finales = [d for d in todos_documentos if d.get('esDocumentoExpediente')]
                docs_estados = [d for d in todos_documentos if not d.get('esDocumentoExpediente')]
                
                print(f'[Publicaciones] Total final: {len(publicaciones)} publicaciones, {len(docs_estados)} docs de estado, {len(docs_expediente_finales)} docs de expediente')
                
                # Devolver JSON con los resultados
                result = {
                    'success': True,
                    'total': len(publicaciones),
                    'totalDocumentos': len(todos_documentos),
                    'totalDocumentosExpediente': len(docs_expediente_finales),
                    'totalDocumentosEstado': len(docs_estados),
                    'publicaciones': publicaciones,
                    'documentos': docs_estados,
                    'documentosExpediente': docs_expediente_finales,
                    'estadosConsultados': estados_consultados,
                    'estadosTotalesEncontrados': len(urls_detalle),
                    'consultaProfunda': consulta_profunda,
                    'parametros': {
                        'fechaInicio': fecha_inicio,
                        'fechaFin': fecha_fin,
                        'idDespacho': id_despacho
                    },
                    'html_size': len(html_data),
                    'nota': f'Se consultaron {estados_consultados} de {len(urls_detalle)} estados. Usa &maxEstados=N para consultar más (máximo 50).'
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
