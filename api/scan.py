from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone
import urllib.request
import xml.etree.ElementTree as ET

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

        feeds = [
            "https://www.reddit.com/r/forhire/new/.rss",
            "https://www.reddit.com/r/freelance_forhire/new/.rss",
            "https://www.reddit.com/r/slavelabour/new/.rss"
        ]
        
        pain_leads = []
        current_time = datetime.now(timezone.utc)
        
        pain_keywords = ["need", "script", "python", "automate", "spreadsheet", "data", "fix", "error", "bot", "web scraper", "app", "excel", "sheet", "work"]
        req_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        for feed_url in feeds:
            try:
                req = urllib.request.Request(feed_url, headers=req_headers)
                with urllib.request.urlopen(req, timeout=5) as response:
                    xml_data = response.read()
                    
                root = ET.fromstring(xml_data)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                
                for entry in root.findall('atom:entry', ns):
                    title_elem = entry.find('atom:title', ns)
                    link_elem = entry.find('atom:link', ns)
                    updated_elem = entry.find('atom:updated', ns)
                    author_elem = entry.find('atom:author/atom:name', ns)
                    
                    if title_elem is not None and link_elem is not None and updated_elem is not None:
                        title = title_elem.text or ""
                        link = link_elem.attrib.get('href', '#')
                        updated_str = updated_elem.text
                        author = author_elem.text if author_elem is not None else "Anonymous User"
                        
                        clean_time = updated_str.replace("Z", "+00:00")
                        post_time = datetime.fromisoformat(clean_time)
                        time_diff = current_time - post_time
                        minutes_ago = int(time_diff.total_seconds() / 60)
                        
                        # Widened to 24 hours (1440 mins) so you see active leads immediately
                        if minutes_ago <= 1440 and any(kw in title.lower() for kw in pain_keywords):
                            pain_leads.append({
                                "title": title,
                                "link": link,
                                "author": author,
                                "time_ago": max(0, minutes_ago)
                            })
            except Exception:
                continue

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
                <h2>🎯 High-Intent Pain Leads Found: {len(pain_leads)}</h2>
        """
        
        if pain_leads:
            for lead in pain_leads:
                results_html += f"""
                <div class="lead-card">
                    <a href="{lead['link']}" target="_blank" class="lead-title">{lead['title']}</a>
                    <p><span class="badge">🔥 Hot Lead</span> &nbsp; <b>Posted:</b> {lead['time_ago']} mins ago &nbsp;|&nbsp; <b>User Handle:</b> u/{lead['author']}</p>
                </div>
                """
        else:
            results_html += "<p style='color: #94a3b8; margin-top: 20px;'>No results found right now. Check back shortly!</p>"
            
        results_html += "</div></body></html>"
        
        self.wfile.write(results_html.encode('utf-8'))
