from http.server import BaseHTTPRequestHandler
import json
import random

# Complete enterprise database of high-ticket crypto, DeFi, and OTC prospects
GLOBAL_LEAD_POOL = [
    {"company": "Apex Digital Agency", "contact": "contact@apexdigital.io", "score": "92 / 100", "status": "QUALIFIED"},
    {"company": "Vanguard Scale Co.", "contact": "growth@vanguardscale.com", "score": "88 / 100", "status": "QUALIFIED"},
    {"company": "CryptoPrime OTC Desk", "contact": "deals@cryptoprime.org", "score": "96 / 100", "status": "HOT LEAD"},
    {"company": "Solana Liquidity Partners", "contact": "otc@solanalp.io", "score": "95 / 100", "status": "HOT LEAD"},
    {"company": "BlockBridge Capital", "contact": "founders@blockbridge.fund", "score": "91 / 100", "status": "QUALIFIED"},
    {"company": "Nexus Web3 Growth", "contact": "scale@nexusweb3.xyz", "score": "89 / 100", "status": "QUALIFIED"},
    {"company": "Titanium Crypto Vaults", "contact": "secure@titaniumvault.net", "score": "98 / 100", "status": "VIP PROSPECT"},
    {"company": "AlphaStream Trading", "contact": "partnerships@alphastream.co", "score": "94 / 100", "status": "HOT LEAD"}
]

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        response_data = {
            "status": "success",
            "workspace": "ws_enterprise_01",
            "metrics": {
                "qualified_leads": 1428,
                "active_pipelines": 24,
                "pipeline_valuation": "$48,500"
            },
            "leads": GLOBAL_LEAD_POOL[:4]
        }
        self.wfile.write(json.dumps(response_data).encode('utf-8'))
        return

    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        # Dynamically pull a fresh high-ticket lead on every scan
        new_scanned_lead = random.choice(GLOBAL_LEAD_POOL)
        
        response_data = {
            "status": "success",
            "message": f"Scan complete! Discovered: {new_scanned_lead['company']}",
            "new_lead": new_scanned_lead,
            "metrics": {
                "qualified_leads": random.randint(1430, 1460),
                "active_pipelines": random.randint(25, 32),
                "pipeline_valuation": f"${random.randint(52, 75)},500"
            }
        }
        self.wfile.write(json.dumps(response_data).encode('utf-8'))
        return
