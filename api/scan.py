from http.server import BaseHTTPRequestHandler
import urllib.request
import json

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

        pain_leads = []
        
        # Pulling live, UNANSWERED Python questions from across the world where people need immediate help
        api_url = "https://api.stackexchange.com/2.3/questions/unanswered?order=desc&sort=activity&tagged=python&site=stackoverflow"
        
        req_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

        try:
            req = urllib.request.Request(api_url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=6) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                for item in data.get('items', []):
                    title = item.get('title', '')
                    link = item.get('link', '#')
                    owner = item.get('owner', {})
                    author = owner.get('display_name', 'Global User')
                    
                    # Filter for practical automation, data, file, script, or error keywords
                    title_lower = title.lower()
                    target_keywords = ['script', 'automate', 'automation', 'excel', 'csv', 'data', 'file', 'error', 'bot', 'scraper', 'parse', 'read', 'write']
                    
                    if any(kw in title_lower for kw in target_keywords):
                        pain_leads.append({
                            "title": title,
                            "link": link,
                            "author": author,
                            "views": item.get('view_count', 0)
                        })
                        
                    if len(pain_leads) >= 12:
                        break
        except Exception as e:
            pass

        # Fallback if specific keywords are quiet, grab general unanswered Python questions
        if not pain_leads:
            try:
                req = urllib.request.Request(api_url, headers=req_headers)
                with urllib.request.urlopen(req, timeout=6) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    for item in data.get('items', [])[:10]:
                        pain_leads.append({
                            "title": item.get('title', ''),
                            "link": item.get('link', '#'),
                            "author": item.get('owner', {}).get('display_name', 'Global User'),
                            "views": item.get('view_count', 0)
                        })
            except:
                pass

        # Render clean, high-impact results page
        results_html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>100% Real-World Unanswered Python Problems</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 25px; }}
                .container {{ max-width: 900px; margin: 0 auto; }}
                .lead-card {{ background: #1e293b; border: 1px solid #334155; padding: 20px; border-radius: 10px; margin-top: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.2); }}
                .lead-title {{ color: #38bdf8; text-decoration: none; font-weight: bold; font-size: 1.15rem; display: block; margin-bottom: 10px; }}
                .lead-title:hover {{ text-decoration: underline; }}
                .back {{ color: #94a3b8; display: inline-block; margin-bottom: 20px; text-decoration: none; font-weight: bold; }}
                .back:hover {{ color: #f8fafc; }}
                p {{ color: #cbd5e1; margin: 0; font-size: 0.95rem; }}
                .badge {{ background: #10b981; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="container">
                <a href="/" class="back">← Back to Home</a>
                <h2>🎯 Active Unanswered Python Problems Found: {len(pain_leads)}</h2>
        """
        
        if pain_leads:
            for lead in pain_leads:
                results_html += f"""
                <div class="lead-card">
                    <a href="{lead['link']}" target="_blank" class="lead-title">{lead['title']}</a>
                    <p><span class="badge">🔥 Unanswered Problem</span> &nbsp; <b>User:</b> {lead['author']} &nbsp;|&nbsp; <b>Views:</b> {lead['views']}</p>
                </div>
                """
        else:
            results_html += "<p style='color: #94a3b8; margin-top: 20px;'>Scanning global unanswered feeds... Refresh in a moment!</p>"
            
        results_html += "</div></body></html>"
        
        self.wfile.write(results_html.encode('utf-8'))
