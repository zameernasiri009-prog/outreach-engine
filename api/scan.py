from http.server import BaseHTTPRequestHandler
import urllib.request
import json

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

        pain_leads = []
        
        # Pulling live open-source project requests from a public developer repository feed
        api_url = "https://api.github.com/search/issues?q=is:issue+is:open+python+automation+script"
        req_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

        try:
            req = urllib.request.Request(api_url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                for item in data.get('items', [])[:10]:
                    pain_leads.append({
                        "title": item.get('title'),
                        "link": item.get('html_url'),
                        "author": item.get('user', {}).get('login', 'Developer'),
                        "time_ago": "Active"
                    })
        except Exception as e:
            pass

        # Render results page
        results_html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Global Pain Leads</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 25px; }}
                .container {{ max-width: 900px; margin: 0 auto; }}
                .lead-card {{ background: #1e293b; border: 1px solid #334155; padding: 20px; border-radius: 10px; margin-top: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.2); }}
                .lead-title {{ color: #38bdf8; text-decoration: none; font-weight: bold; font-size: 1.15rem; display: block; margin-bottom: 10px; }}
                .lead-title:hover {{ text-decoration: underline; }}
                .back {{ color: #94a3b8; display: inline-block; margin-bottom: 20px; text-decoration: none; font-weight: bold; }}
                .back:hover {{ color: #f8fafc; }}
                p {{ color: #cbd5e1; margin: 0; font-size: 0.95rem; }}
                .badge {{ background: #0284c7; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="container">
                <a href="/" class="back">← Back to Home</a>
                <h2>🎯 Global Intent Leads Found: {len(pain_leads)}</h2>
        """
        
        if pain_leads:
            for lead in pain_leads:
                results_html += f"""
                <div class="lead-card">
                    <a href="{lead['link']}" target="_blank" class="lead-title">{lead['title']}</a>
                    <p><span class="badge">🔥 Verified Lead</span> &nbsp; <b>Status:</b> {lead['time_ago']} &nbsp;|&nbsp; <b>User Handle:</b> @{lead['author']}</p>
                </div>
                """
        else:
            results_html += "<p style='color: #94a3b8; margin-top: 20px;'>Scanning... Try clicking back and refreshing!</p>"
            
        results_html += "</div></body></html>"
        
        self.wfile.write(results_html.encode('utf-8'))
