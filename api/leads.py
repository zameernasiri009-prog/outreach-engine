from http.server import BaseHTTPRequestHandler
from datetime import datetime
import urllib.request
import xml.etree.ElementTree as ET

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

        # Global high-intent public boards
        feeds = [
            "https://www.reddit.com/r/forhire/new/.rss",
            "https://www.reddit.com/r/freelance_forhire/new/.rss",
            "https://www.reddit.com/r/slavelabour/new/.rss"
        ]
        
        pain_leads = []
        current_time = datetime.utcnow()
        
        # Keywords indicating urgent spreadsheet, data, or automation pain
        pain_keywords = ["need", "script", "python", "automate", "spreadsheet", "data", "fix", "error", "bot", "web scraper", "app", "excel"]
        
        # Neutral browser header for complete anonymity
        req_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

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
                        
                        clean_time = updated_str.replace("Z", "")
                        post_time = datetime.fromisoformat(clean_time)
                        time_diff = current_time - post_time
                        minutes_ago = int(time_diff.total_seconds() / 60)
                        
                        # Filter for posts within the last 60 minutes containing pain triggers
                        if minutes_ago <= 60 and any(kw in title.lower() for kw in pain_keywords):
                            pain_leads.append({
                                "title": title,
                                "link": link,
                                "author": author,
                                "time_ago": max(0, minutes_ago)
                            })
            except Exception:
                continue

        # HTML Results Page
        results_html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Scan Results</title>
            <style>
                body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 25px; }}
                .container {{ max-width: 900px; margin: 0 auto; }}
                .lead-card {{ background: #1e293b; border: 1px solid #334155; padding: 18px; border-radius: 8px; margin-top: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
                .lead-title {{ color: #38bdf8; text-decoration: none; font-weight: bold; font-size: 1.1rem; display: block; margin-bottom: 8px; }}
                .lead-title:hover {{ text-decoration: underline; }}
                .back {{ color: #94a3b8; display: inline-block; margin-bottom: 20px; text-decoration: none; font-weight: bold; }}
                .back:hover {{ color: #f8fafc; }}
                p {{ color: #cbd5e1; margin: 0; font-size: 0.95rem; }}
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
                    <p><b>Posted:</b> {lead['time_ago']} minutes ago &nbsp;|&nbsp; <b>User / Handle:</b> u/{lead['author']}</p>
                </div>
                """
        else:
            results_html += "<p style='color: #94a3b8; margin-top: 20px;'>No critical pain points detected right this second. Click back and scan again shortly!</p>"
            
        results_html += "</div></body></html>"
        
        self.wfile.write(results_html.encode('utf-8'))

