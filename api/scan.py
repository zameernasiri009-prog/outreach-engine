from http.server import BaseHTTPRequestHandler
import urllib.request
import json
import urllib.parse

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

        pain_leads = []
        
        # Strict search targeting active bugs, errors, and automation help requests
        query = 'is:issue is:open python (error OR bug OR "help me" OR script OR automation OR crash)'
        encoded_query = urllib.parse.quote(query)
        api_url = f"https://api.github.com/search/issues?q={encoded_query}&sort=updated&order=desc"
        
        req_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

        try:
            req = urllib.request.Request(api_url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=6) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                for item in data.get('items', []):
                    title = item.get('title', '')
                    title_lower = title.lower()
                    
                    # Exclude non-client noise (tutorials, roadmaps, documentation updates)
                    if any(exclude in title_lower for exclude in ['tutorial', 'roadmap', 'learning', 'course', 'docs', 'policy', 'refactor']):
                        continue
                        
                    pain_leads.append({
                        "title": title,
                        "link": item.get('html_url'),
                        "author": item.get('user', {}).get('login', 'Client'),
                        "comments": item.get('comments', 0)
                    })
                    
                    if len(pain_leads) >= 12:
                        break
        except Exception as e:
            pass

        # Render clean, high-impact results page
        results_html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Immediate Python Pain Leads</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 25px; }}
                .container {{ max-width: 900px; margin: 0 auto; }}
                .lead-card {{ background: #1e293b; border: 1px solid #334155; padding: 20px; border-radius: 10px; margin-top: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.2); }}
                .lead-title {{ color: #38bdf8; text-decoration: none; font-weight: bold; font-size: 1.15rem; display: block; margin-bottom: 10px; }}
                .lead-title:hover {{ text-decoration: underline; }}
                .back {{ color: #94a3b8; display: inline-block; margin-bottom: 20px; text-decoration: none; font-weight: bold; }}
                .back:hover {{ color: #f8fafc; }}
                p {{ color: #cbd5e1; margin: 0; font-size: 0.95rem; }}
                .badge {{ background: #ef4444; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="container">
                <a href="/" class="back">← Back to Home</a>
                <h2>🎯 Active Python Pain Leads Found: {len(pain_leads)}</h2>
        """
        
        if pain_leads:
            for lead in pain_leads:
                results_html += f"""
                <div class="lead-card">
                    <a href="{lead['link']}" target="_blank" class="lead-title">{lead['title']}</a>
                    <p><span class="badge">🔥 Urgent Bug/Error</span> &nbsp; <b>Client Handle:</b> @{lead['author']} &nbsp;|&nbsp; <b>Comments/Replies:</b> {lead['comments']}</p>
                </div>
                """
        else:
            results_html += "<p style='color: #94a3b8; margin-top: 20px;'>Scanning for active errors... Refresh in a moment!</p>"
            
        results_html += "</div></body></html>"
        
        self.wfile.write(results_html.encode('utf-8'))
