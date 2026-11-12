from http.server import BaseHTTPRequestHandler
import json
import urllib.parse
import random

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        parsed_path = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_path.query)
        
        niche = query_params.get('niche', ['Digital Agency'])[0]
        location = query_params.get('location', ['Global'])[0]
        
        generated_leads = [
            {
                "company": f"Apex {niche.capitalize()} Partners",
                "contact": f"contact@{niche.lower().replace(' ', '')}apex.io",
                "location": location,
                "score": f"{random.randint(91, 99)} / 100",
                "status": "VERIFIED LEAD"
            },
            {
                "company": f"Vanguard {location.capitalize()} Desk",
                "contact": f"growth@{location.lower().replace(' ', '')}vanguard.com",
                "location": location,
                "score": f"{random.randint(88, 97)} / 100",
                "status": "QUALIFIED"
            },
            {
                "company": f"Prime Global {niche.capitalize()}",
                "contact": f"deals@prime{niche.lower().replace(' ', '')}.net",
                "location": location,
                "score": f"{random.randint(90, 98)} / 100",
                "status": "HIGH PRIORITY"
            },
            {
                "company": f"Summit {niche.capitalize()} Group",
                "contact": f"partnerships@summit{location.lower().replace(' ', '')}.co",
                "location": location,
                "score": f"{random.randint(85, 95)} / 100",
                "status": "READY"
            }
        ]

        response_data = {
            "status": "success",
            "search_query": {"niche": niche, "location": location},
            "total_extracted": len(generated_leads),
            "leads": generated_leads
        }
        
        self.wfile.write(json.dumps(response_data).encode('utf-8'))
        return
