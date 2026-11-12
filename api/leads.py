from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        leads_data = {
            "status": "success",
            "workspace": "ws_enterprise_01",
            "metrics": {
                "qualified_leads": 1428,
                "active_pipelines": 24,
                "pipeline_valuation": "$48,500"
            },
            "leads": [
                {"company": "Apex Digital Agency", "contact": "contact@apexdigital.io", "score": "92 / 100", "status": "QUALIFIED"},
                {"company": "Vanguard Scale Co.", "contact": "growth@vanguardscale.com", "score": "88 / 100", "status": "QUALIFIED"}
            ]
        }
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(leads_data).encode('utf-8'))
        return
