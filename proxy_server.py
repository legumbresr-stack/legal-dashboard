#!/usr/bin/env python3
"""
Servidor proxy local para evitar CORS con la API de Rama Judicial.
Ejecutar: python proxy_server.py
Luego abrir: http://localhost:8000
"""

import http.server
import socketserver
import urllib.request
import urllib.error
import json
import ssl

PORT = 8000

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Si es una petición al proxy de Rama Judicial
        if self.path.startswith('/api/rama/'):
            self.proxy_rama_judicial()
        else:
            # Servir archivos estáticos normalmente
            super().do_GET()
    
    def proxy_rama_judicial(self):
        # Extraer la URL real
        rama_path = self.path.replace('/api/rama/', '')
        rama_url = f'https://consultaprocesos.ramajudicial.gov.co:448/api/v2/{rama_path}'
        
        print(f'Proxy request: {rama_url}')
        
        try:
            # Crear contexto SSL que no verifica certificados (necesario para algunos servidores)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            # Hacer la petición a Rama Judicial
            req = urllib.request.Request(rama_url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            req.add_header('Accept', 'application/json')
            
            with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                data = response.read()
                
                # Enviar respuesta con headers CORS
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
    
    def do_OPTIONS(self):
        # Manejar preflight CORS
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()

if __name__ == '__main__':
    with socketserver.TCPServer(("", PORT), ProxyHandler) as httpd:
        print(f"===========================================")
        print(f"  Servidor con Proxy CORS iniciado")
        print(f"  Abrir en navegador: http://localhost:{PORT}")
        print(f"===========================================")
        print(f"Presiona Ctrl+C para detener el servidor")
        httpd.serve_forever()
