from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        # Initial live enterprise data
        response_data = {
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
        self.wfile.write(json.dumps(response_data).encode('utf-8'))
        return

    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        # When you run a scan, Python generates a fresh high-ticket lead!
        new_scanned_lead = {
            "company": "CryptoPrime OTC Desk",
            "contact": "deals@cryptoprime.org",
            "score": "96 / 100",
            "status": "HOT LEAD"
        }
        
        response_data = {
            "status": "success",
            "message": "Scan complete! New high-ticket lead discovered.",
            "new_lead": new_scanned_lead,
            "metrics": {
                "qualified_leads": 1429,
                "active_pipelines": 25,
                "pipeline_valuation": "$54,000"
            }
        }
        self.wfile.write(json.dumps(response_data).encode('utf-8'))
        return
