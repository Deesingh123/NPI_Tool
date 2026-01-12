import streamlit as st
import json
from datetime import datetime
import hashlib
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import os
import pickle
import base64
import requests
import io
from PIL import Image
from reportlab.lib.pagesizes import letter, A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import tempfile
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, PageBreak, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import concurrent.futures
import time
from googleapiclient.http import MediaIoBaseDownload

# Page config
st.set_page_config(page_title="Google Slides Team Manager", layout="wide")

# Constants
SCOPES = ['https://www.googleapis.com/auth/presentations.readonly', 
          'https://www.googleapis.com/auth/drive.readonly',
          'https://www.googleapis.com/auth/drive']
TOKEN_FILE = 'token.pickle'
SHARED_DB_FILE = 'shared_slides_db.json'

def initialize_shared_state():
    """Initialize or load shared state across all sessions"""
    try:
        if os.path.exists(SHARED_DB_FILE):
            with open(SHARED_DB_FILE, 'r') as f:
                data = json.load(f)
                if 'users' not in data:
                    data['users'] = {'admin': {'password': hashlib.sha256('admin123'.encode()).hexdigest(), 'role': 'admin'}}
                if 'slides' not in data:
                    data['slides'] = []
                if 'activities' not in data:
                    data['activities'] = []
                return data
    except:
        pass
    
    return {
        'users': {
            'admin': {'password': hashlib.sha256('admin123'.encode()).hexdigest(), 'role': 'admin'}
        },
        'slides': [],
        'activities': []
    }

def save_shared_state():
    """Save shared state to file"""
    try:
        with open(SHARED_DB_FILE, 'w') as f:
            json.dump(st.session_state.shared_data, f, indent=2)
    except:
        pass

def load_shared_state():
    """Load shared state from file"""
    try:
        if os.path.exists(SHARED_DB_FILE):
            with open(SHARED_DB_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return None

def merge_shared_state():
    """Merge file data with session state"""
    file_data = load_shared_state()
    if not file_data:
        return
    
    session_data = st.session_state.shared_data
    
    # Merge users
    if 'users' in file_data:
        for username, user_data in file_data['users'].items():
            if username not in session_data['users']:
                session_data['users'][username] = user_data
    
    # Merge slides - use presentation_id as unique key
    if 'slides' in file_data:
        current_slide_ids = {slide['presentation_id'] for slide in session_data['slides']}
        
        for file_slide in file_data['slides']:
            if file_slide['presentation_id'] not in current_slide_ids:
                session_data['slides'].append(file_slide)
            else:
                # Update existing slide if newer
                for i, session_slide in enumerate(session_data['slides']):
                    if session_slide['presentation_id'] == file_slide['presentation_id']:
                        # Keep the one with newer last_modified timestamp
                        session_time = session_slide.get('last_modified', '')
                        file_time = file_slide.get('last_modified', '')
                        
                        if file_time > session_time:
                            session_data['slides'][i] = file_slide
                        break
    
    # Merge activities
    if 'activities' in file_data:
        for file_act in file_data['activities']:
            if file_act not in session_data['activities']:
                session_data['activities'].append(file_act)
    
    save_shared_state()

def refresh_shared_state():
    """Refresh session state with latest shared data"""
    try:
        merge_shared_state()
        return True
    except:
        return False

# Initialize session state
if 'shared_data' not in st.session_state:
    st.session_state.shared_data = initialize_shared_state()
    save_shared_state()

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'current_user' not in st.session_state:
    st.session_state.current_user = None
if 'google_creds' not in st.session_state:
    st.session_state.google_creds = None
if 'flow' not in st.session_state:
    st.session_state.flow = None
if 'show_register' not in st.session_state:
    st.session_state.show_register = False
if 'show_merged_view' not in st.session_state:
    st.session_state.show_merged_view = False
if 'combined_pdf' not in st.session_state:
    st.session_state.combined_pdf = None
if 'combined_pdf_filename' not in st.session_state:
    st.session_state.combined_pdf_filename = None

# Helper functions
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def authenticate(username, password):
    if username in st.session_state.shared_data['users']:
        if st.session_state.shared_data['users'][username]['password'] == hash_password(password):
            return True
    return False

def get_user_role(username):
    """Get user role with proper refresh from shared data"""
    # First check if we have the user in shared data
    if username in st.session_state.shared_data['users']:
        # Force refresh from shared data
        refresh_shared_state()
        return st.session_state.shared_data['users'][username].get('role', 'member')
    return 'member'

def log_activity(action, user, details):
    """Log user activities"""
    activity = {
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'user': user,
        'action': action,
        'details': details
    }
    st.session_state.shared_data['activities'].append(activity)
    save_shared_state()

def load_credentials():
    """Load credentials from pickle file"""
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'rb') as token:
                creds = pickle.load(token)
                if creds and creds.valid:
                    return creds
        except:
            pass
    return None

def save_credentials(creds):
    """Save credentials to pickle file"""
    try:
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)
    except:
        pass

def get_google_auth_flow():
    """Create OAuth flow from secrets"""
    try:
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        
        client_config = {
            "web": {
                "client_id": st.secrets["auth"]["google"]["client_id"],
                "client_secret": st.secrets["auth"]["google"]["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [st.secrets["auth"]["redirect_uri"]]
            }
        }
        
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=st.secrets["auth"]["redirect_uri"]
        )
        return flow
    except Exception as e:
        st.error(f"Error creating auth flow: {str(e)}")
        return None

def get_google_services():
    """Get Google Slides service"""
    if st.session_state.google_creds is None:
        return None
    
    try:
        slides_service = build('slides', 'v1', credentials=st.session_state.google_creds)
        drive_service = build('drive', 'v3', credentials=st.session_state.google_creds)
        return slides_service, drive_service
    except:
        return None, None

def get_presentation_details(slides_service, presentation_id):
    """Fetch presentation details from Google Slides"""
    try:
        presentation = slides_service.presentations().get(presentationId=presentation_id).execute()
        return {
            'title': presentation.get('title', 'Untitled'),
            'slide_count': len(presentation.get('slides', [])),
            'slides': presentation.get('slides', []),
            'revision_id': presentation.get('revisionId', 'unknown')
        }
    except:
        return None

def check_for_updates(slides_service):
    """Check if any presentations have been updated"""
    if slides_service is None:
        return False
    
    refresh_shared_state()
    
    updates_found = False
    slides_list = st.session_state.shared_data['slides']
    
    for idx, slide in enumerate(slides_list):
        try:
            details = get_presentation_details(slides_service, slide['presentation_id'])
            if details:
                current_count = slide.get('slide_count', 0)
                if details['slide_count'] != current_count:
                    slides_list[idx]['slide_count'] = details['slide_count']
                    slides_list[idx]['last_modified'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    slides_list[idx]['title'] = details['title']
                    updates_found = True
                    
                    log_activity("SLIDE_UPDATE", slide['uploader'], 
                               f"Updated '{slide['title']}' from {current_count} to {details['slide_count']} slides")
        
        except:
            continue
    
    if updates_found:
        save_shared_state()
    
    return updates_found

def render_slide_in_streamlit(presentation_id, slide_idx=0):
    """Render Google Slides presentation in Streamlit using iframe"""
    embed_url = f"https://docs.google.com/presentation/d/{presentation_id}/embed?start=false&loop=false&delayms=3000&slide=id.p{slide_idx}"
    
    iframe_html = f"""
    <iframe src="{embed_url}" 
            frameborder="0" 
            width="100%" 
            height="600" 
            allowfullscreen="true" 
            mozallowfullscreen="true" 
            webkitallowfullscreen="true">
    </iframe>
    """
    return iframe_html

def export_slide_as_image(drive_service, presentation_id, slide_number, width=800):
    """Export a specific slide as image using Drive API"""
    try:
        # Export the slide as PNG using Drive export API
        request = drive_service.files().export_media(
            fileId=presentation_id,
            mimeType='image/png',
            pageRange=f"{slide_number}"
        )
        
        # Download the image
        image_data = io.BytesIO()
        downloader = MediaIoBaseDownload(image_data, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        image_data.seek(0)
        return image_data.getvalue()
    except Exception as e:
        st.error(f"Error exporting slide {slide_number}: {str(e)}")
        return None

def download_slide_image(presentation_id, slide_number, access_token):
    """Download slide as image using export API"""
    try:
        # Alternative method using export URL
        export_url = f"https://docs.google.com/presentation/d/{presentation_id}/export/png?page={slide_number}"
        
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        
        response = requests.get(export_url, headers=headers, stream=True)
        
        if response.status_code == 200:
            return io.BytesIO(response.content)
        else:
            return None
    except Exception as e:
        return None

def create_image_combined_pdf(slides_list):
    """Create a combined PDF with actual slide images"""
    try:
        if st.session_state.google_creds is None:
            st.error("Google credentials not available")
            return None
        
        # Create a temporary file for the PDF
        temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        temp_pdf_path = temp_pdf.name
        temp_pdf.close()
        
        # Create PDF document
        doc = SimpleDocTemplate(temp_pdf_path, pagesize=A4,
                                topMargin=0.5*inch, bottomMargin=0.5*inch,
                                leftMargin=0.5*inch, rightMargin=0.5*inch)
        
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            spaceAfter=30,
            alignment=1,  # Center
            textColor=HexColor('#2C3E50')
        )
        
        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Normal'],
            fontSize=14,
            spaceAfter=20,
            alignment=1,
            textColor=HexColor('#7F8C8D')
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=18,
            spaceAfter=15,
            spaceBefore=20,
            textColor=HexColor('#3498DB')
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=11,
            spaceAfter=10
        )
        
        story = []
        
        # Title Page
        story.append(Paragraph("Team Slides Combined Report", title_style))
        story.append(Spacer(1, 0.2*inch))
        story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y %H:%M:%S')}", subtitle_style))
        story.append(Spacer(1, 0.4*inch))
        
        # Team Statistics
        all_uploaders = list(set(slide['uploader'] for slide in slides_list))
        total_slides = sum(slide.get('slide_count', 0) for slide in slides_list)
        
        stats_text = f"""
        <b>Team Members:</b> {len(all_uploaders)}<br/>
        <b>Total Presentations:</b> {len(slides_list)}<br/>
        <b>Total Slides:</b> {total_slides}<br/>
        <b>Team Members:</b> {', '.join(all_uploaders)}
        """
        story.append(Paragraph(stats_text, normal_style))
        story.append(PageBreak())
        
        # Get Google services
        slides_service, drive_service = get_google_services()
        if not slides_service or not drive_service:
            st.error("Unable to access Google services")
            return None
        
        # Get access token for export
        access_token = st.session_state.google_creds.token
        
        # Process each presentation
        for slide_idx, slide in enumerate(slides_list):
            # Presentation header
            story.append(Paragraph(f"Presentation {slide_idx + 1}: {slide.get('title', 'Untitled')}", heading_style))
            
            # Presentation details
            details_text = f"""
            <b>Uploader:</b> {slide.get('uploader', 'Unknown')}<br/>
            <b>Slides:</b> {slide.get('slide_count', 0)}<br/>
            <b>Uploaded:</b> {slide.get('upload_date', '')[:10]}<br/>
            """
            if slide.get('description'):
                details_text += f"<b>Description:</b> {slide.get('description', '')}<br/>"
            
            story.append(Paragraph(details_text, normal_style))
            story.append(Spacer(1, 0.2*inch))
            
            # Download and add each slide image
            slide_count = slide.get('slide_count', 0)
            
            # Show progress in Streamlit
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for i in range(slide_count):
                status_text.text(f"Downloading slide {i+1} of {slide_count} from '{slide.get('title', 'Untitled')}'...")
                progress_bar.progress((i + 1) / slide_count)
                
                # Try multiple methods to get slide image
                image_data = None
                
                # Method 1: Try export URL with token
                try:
                    export_url = f"https://docs.google.com/presentation/d/{slide['presentation_id']}/export/png?page={i+1}"
                    headers = {'Authorization': f'Bearer {access_token}'}
                    response = requests.get(export_url, headers=headers)
                    
                    if response.status_code == 200:
                        image_data = io.BytesIO(response.content)
                except:
                    pass
                
                # Method 2: Try iframe method as fallback
                if not image_data:
                    try:
                        # Use iframe screenshot method (requires internet)
                        screenshot_url = f"https://docs.google.com/presentation/d/{slide['presentation_id']}/embed?start=false&loop=false&delayms=3000&slide=id.p{i}"
                        # Note: This would require a headless browser or external service
                        # For now, we'll use a placeholder
                        pass
                    except:
                        pass
                
                if image_data:
                    try:
                        # Create image object
                        img = RLImage(image_data)
                        img.drawHeight = 5.5*inch  # Set height
                        img.drawWidth = 8.5*inch   # Set width
                        img.hAlign = 'CENTER'
                        story.append(img)
                        story.append(Spacer(1, 0.1*inch))
                        
                        # Add slide number
                        slide_num_text = f"<b>Slide {i+1}</b>"
                        story.append(Paragraph(slide_num_text, normal_style))
                        story.append(Spacer(1, 0.2*inch))
                    except Exception as e:
                        # Add placeholder if image fails
                        placeholder_text = f"[Slide {i+1} image could not be loaded]"
                        story.append(Paragraph(placeholder_text, normal_style))
                else:
                    # Add placeholder for failed slide
                    placeholder_text = f"[Slide {i+1} - Image unavailable]"
                    story.append(Paragraph(placeholder_text, normal_style))
                    story.append(Spacer(1, 0.1*inch))
            
            # Clear progress
            progress_bar.empty()
            status_text.empty()
            
            # Add page break between presentations
            if slide_idx < len(slides_list) - 1:
                story.append(PageBreak())
        
        # Build PDF
        doc.build(story)
        
        # Read the PDF file
        with open(temp_pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        
        # Clean up temporary file
        os.unlink(temp_pdf_path)
        
        return pdf_bytes
    
    except Exception as e:
        st.error(f"Error creating image PDF: {str(e)}")
        import traceback
        st.error(traceback.format_exc())
        return None

def create_simple_combined_pdf(slides_list):
    """Create a simple combined PDF without images (fallback)"""
    try:
        temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        temp_pdf_path = temp_pdf.name
        temp_pdf.close()
        
        c = canvas.Canvas(temp_pdf_path, pagesize=letter)
        width, height = letter
        
        # Title page
        c.setFont("Helvetica-Bold", 24)
        c.drawCentredString(width/2, height - 100, "Team Slides Combined Report")
        
        c.setFont("Helvetica", 14)
        c.drawCentredString(width/2, height - 140, datetime.now().strftime('%B %d, %Y %H:%M:%S'))
        
        # Team info
        all_uploaders = list(set(slide['uploader'] for slide in slides_list))
        total_slides = sum(slide.get('slide_count', 0) for slide in slides_list)
        
        c.setFont("Helvetica", 12)
        c.drawString(100, height - 200, f"Team Members: {', '.join(all_uploaders)}")
        c.drawString(100, height - 220, f"Total Presentations: {len(slides_list)}")
        c.drawString(100, height - 240, f"Total Slides: {total_slides}")
        
        c.showPage()
        
        # Add each presentation
        for slide_idx, slide in enumerate(slides_list):
            # Presentation header
            c.setFont("Helvetica-Bold", 18)
            c.drawString(100, height - 100, f"Presentation {slide_idx + 1}: {slide.get('title', 'Untitled')}")
            
            c.setFont("Helvetica", 12)
            c.drawString(100, height - 130, f"Uploader: {slide.get('uploader', 'Unknown')}")
            c.drawString(100, height - 150, f"Slides: {slide.get('slide_count', 0)}")
            c.drawString(100, height - 170, f"Uploaded: {slide.get('upload_date', '')[:10]}")
            
            # Add description if available
            if slide.get('description'):
                c.drawString(100, height - 190, f"Description: {slide.get('description', '')[:100]}")
            
            # Add slide placeholders
            y_pos = height - 220
            for i in range(slide.get('slide_count', 0)):
                if y_pos < 100:
                    c.showPage()
                    y_pos = height - 100
                    c.setFont("Helvetica", 10)
                
                c.rect(100, y_pos - 60, 400, 60, stroke=1, fill=0)
                c.setFont("Helvetica", 10)
                c.drawString(110, y_pos - 20, f"Slide {i+1} - {slide.get('title', 'Untitled')}")
                c.drawString(110, y_pos - 35, "[Slide image would appear here with proper permissions]")
                
                y_pos -= 80
            
            if slide_idx < len(slides_list) - 1:
                c.showPage()
        
        c.save()
        
        with open(temp_pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        
        os.unlink(temp_pdf_path)
        
        return pdf_bytes
    
    except Exception as e:
        st.error(f"Error creating simple PDF: {str(e)}")
        return None

def create_html_image_view(slides_list):
    """Create an HTML file with embedded slide images"""
    try:
        if st.session_state.google_creds is None:
            return None
        
        access_token = st.session_state.google_creds.token
        all_uploaders = list(set(slide['uploader'] for slide in slides_list))
        total_slides = sum(slide.get('slide_count', 0) for slide in slides_list)
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Team Slides Combined View</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 40px;
                    background-color: #f5f5f5;
                }}
                .header {{
                    background-color: #2C3E50;
                    color: white;
                    padding: 30px;
                    border-radius: 10px;
                    margin-bottom: 30px;
                }}
                .presentation {{
                    background-color: white;
                    border-radius: 10px;
                    padding: 20px;
                    margin-bottom: 30px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .slide-container {{
                    margin: 20px 0;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    padding: 10px;
                    background-color: #fafafa;
                }}
                .slide-image {{
                    max-width: 100%;
                    height: auto;
                    display: block;
                    margin: 0 auto;
                }}
                .slide-info {{
                    text-align: center;
                    margin-top: 10px;
                    color: #666;
                    font-size: 14px;
                }}
                .stats {{
                    display: flex;
                    justify-content: space-around;
                    background-color: #3498DB;
                    color: white;
                    padding: 15px;
                    border-radius: 5px;
                    margin: 20px 0;
                }}
                .stat-item {{
                    text-align: center;
                }}
                h1, h2, h3 {{
                    color: #2C3E50;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>📊 Team Slides Combined View</h1>
                <p>Generated: {datetime.now().strftime('%B %d, %Y %H:%M:%S')}</p>
            </div>
            
            <div class="stats">
                <div class="stat-item">
                    <h3>👥 Team Members</h3>
                    <p>{len(all_uploaders)}</p>
                </div>
                <div class="stat-item">
                    <h3>📑 Presentations</h3>
                    <p>{len(slides_list)}</p>
                </div>
                <div class="stat-item">
                    <h3>📄 Total Slides</h3>
                    <p>{total_slides}</p>
                </div>
            </div>
        """
        
        for slide_idx, slide in enumerate(slides_list):
            html_content += f"""
            <div class="presentation">
                <h2>Presentation {slide_idx + 1}: {slide.get('title', 'Untitled')}</h2>
                <p><strong>Uploader:</strong> {slide.get('uploader', 'Unknown')} | 
                   <strong>Slides:</strong> {slide.get('slide_count', 0)} | 
                   <strong>Uploaded:</strong> {slide.get('upload_date', '')[:10]}</p>
                
                {f'<p><strong>Description:</strong> {slide.get("description", "")}</p>' if slide.get('description') else ''}
            """
            
            # Add iframe for each slide
            for i in range(slide.get('slide_count', 0)):
                html_content += f"""
                <div class="slide-container">
                    <h3>Slide {i+1}</h3>
                    <iframe 
                        src="https://docs.google.com/presentation/d/{slide['presentation_id']}/embed?start=false&loop=false&delayms=3000&slide=id.p{i}"
                        width="100%" 
                        height="500" 
                        frameborder="0" 
                        allowfullscreen="true">
                    </iframe>
                    <div class="slide-info">
                        {slide.get('title', 'Untitled')} - Slide {i+1} | Uploader: {slide.get('uploader', 'Unknown')}
                    </div>
                </div>
                """
            
            html_content += "</div>"
        
        html_content += """
            <script>
                // Auto-refresh iframes every 30 seconds
                setTimeout(function() {
                    location.reload();
                }, 30000);
            </script>
        </body>
        </html>
        """
        
        return html_content.encode('utf-8')
    
    except Exception as e:
        st.error(f"Error creating HTML view: {str(e)}")
        return None

# Check for saved credentials
if st.session_state.google_creds is None:
    creds = load_credentials()
    if creds:
        st.session_state.google_creds = creds

# Helper function to check admin access
def check_admin_access():
    """Check if current user has admin access"""
    if not st.session_state.logged_in:
        return False
    
    # Force refresh to get latest role data
    refresh_shared_state()
    
    username = st.session_state.current_user
    if username in st.session_state.shared_data['users']:
        role = st.session_state.shared_data['users'][username].get('role', 'member')
        return role == 'admin'
    
    return False

# Sidebar - Authentication with Registration Option
with st.sidebar:
    st.title("🔐 Authentication")
    
    if not st.session_state.logged_in:
        # Tabs for Login and Register
        tab1, tab2 = st.tabs(["Login", "Register"])
        
        with tab1:
            login_username = st.text_input("Username", key="login_user")
            login_password = st.text_input("Password", type="password", key="login_pass")
            
            if st.button("Login", key="login_btn"):
                if authenticate(login_username, login_password):
                    st.session_state.logged_in = True
                    st.session_state.current_user = login_username
                    
                    # Update last login time for the user
                    if login_username in st.session_state.shared_data['users']:
                        st.session_state.shared_data['users'][login_username]['last_login'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        save_shared_state()
                    
                    log_activity("LOGIN", login_username, "User logged in")
                    st.success("Logged in successfully!")
                    st.rerun()
                else:
                    st.error("Invalid credentials")
        
        with tab2:
            reg_username = st.text_input("Username", key="reg_user")
            reg_password = st.text_input("Password", type="password", key="reg_pass")
            reg_password_confirm = st.text_input("Confirm Password", type="password", key="reg_pass_confirm")
            
            if st.button("Register", key="reg_btn"):
                if reg_username in st.session_state.shared_data['users']:
                    st.error("Username already exists")
                elif reg_password != reg_password_confirm:
                    st.error("Passwords don't match")
                elif len(reg_password) < 6:
                    st.error("Password must be at least 6 characters")
                else:
                    st.session_state.shared_data['users'][reg_username] = {
                        'password': hash_password(reg_password),
                        'role': 'member'
                    }
                    save_shared_state()
                    log_activity("REGISTER", reg_username, "New user registered")
                    st.success("Registration successful! Please login.")
                    st.rerun()
    
    else:
        # Show current user info with role
        st.success(f"✅ {st.session_state.current_user}")
        
        # Show role with refresh button
        col_role, col_refresh = st.columns([2, 1])
        with col_role:
            current_role = get_user_role(st.session_state.current_user)
            if current_role == 'admin':
                st.success("👑 Admin")
            else:
                st.info("👤 Member")
        
        with col_refresh:
            if st.button("🔄", key="refresh_role", help="Refresh role"):
                refresh_shared_state()
                st.rerun()
        
        # Google Integration
        st.divider()
        st.subheader("🔗 Google Integration")
        
        if st.session_state.google_creds is None:
            st.warning("Not connected to Google")
            
            if st.button("🔐 Step 1: Get Authorization URL"):
                flow = get_google_auth_flow()
                if flow:
                    st.session_state.flow = flow
                    auth_url, _ = flow.authorization_url(
                        prompt='consent',
                        access_type='offline',
                        include_granted_scopes='true'
                    )
                    st.success("✅ Authorization URL generated!")
                    st.markdown(f"### [🔗 Click here to authorize]({auth_url})")
                    st.info("👆 Click the link above, authorize, then copy the code from the URL")
            
            # Show authorization code input field
            if st.session_state.flow is not None:
                st.markdown("---")
                st.subheader("🔑 Step 2: Enter Code")
                st.info("Copy the code from URL after `code=` and before `&`")
                
                auth_code_input = st.text_input("Paste authorization code:", key="auth_code_input")
                
                if st.button("✅ Submit & Connect"):
                    if auth_code_input:
                        try:
                            st.session_state.flow.fetch_token(code=auth_code_input)
                            st.session_state.google_creds = st.session_state.flow.credentials
                            save_credentials(st.session_state.google_creds)
                            log_activity("GOOGLE_AUTH", st.session_state.current_user, "Connected Google account")
                            st.success("✅ Connected to Google!")
                            st.balloons()
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Error: {str(e)}")
                    else:
                        st.error("Please enter the authorization code")
        else:
            st.success("✅ Connected to Google")
            if st.button("Disconnect"):
                st.session_state.google_creds = None
                st.session_state.flow = None
                if os.path.exists(TOKEN_FILE):
                    os.remove(TOKEN_FILE)
                log_activity("GOOGLE_DISCONNECT", st.session_state.current_user, "Disconnected Google")
                st.rerun()
        
        # Merged View Button in Sidebar
        st.divider()
        if st.button("📊 Generate Combined PDF/HTML"):
            st.session_state.show_merged_view = True
            st.rerun()
        
        if st.session_state.show_merged_view:
            if st.button("❌ Close Combined View"):
                st.session_state.show_merged_view = False
                st.rerun()
        
        st.divider()
        if st.button("Logout"):
            log_activity("LOGOUT", st.session_state.current_user, "User logged out")
            st.session_state.logged_in = False
            st.session_state.current_user = None
            st.session_state.show_merged_view = False
            st.rerun()

# Main content
if not st.session_state.logged_in:
    st.title("🎯 Google Slides Team Manager")
    st.info("👈 Please login or register to continue")
    
    st.markdown("""
    ### Features:
    - 📤 Upload Google Slides presentations
    - 👁️ **All team slides combined in one dashboard**
    - 🔄 Real-time updates
    - 👥 Team collaboration
    - 📊 **Merged PDF with actual slide images**
    - 🌐 **HTML view with embedded slides**
    - 📥 **Download combined files**
    
    ### Setup:
    1. **New users**: Click "Register" tab in sidebar
    2. Login: `admin` / `admin123` (for admin)
    3. Connect Google Account
    4. Upload slides
    """)

else:
    # Auto-refresh shared state
    refresh_shared_state()
    
    # Show Merged View if enabled
    if st.session_state.show_merged_view:
        st.title("📊 All Team Slides - Combined View")
        
        # Refresh data
        refresh_shared_state()
        
        slides_list = st.session_state.shared_data['slides']
        
        if len(slides_list) == 0:
            st.info("No presentations uploaded yet.")
        else:
            # Statistics
            total_slides = sum(slide.get('slide_count', 0) for slide in slides_list)
            all_uploaders = list(set(slide['uploader'] for slide in slides_list))
            
            st.success(f"Found {len(slides_list)} presentations with {total_slides} total slides")
            
            # Show statistics
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("👥 Team Members", len(all_uploaders))
            with col2:
                st.metric("📑 Presentations", len(slides_list))
            with col3:
                st.metric("📄 Total Slides", total_slides)
            
            st.divider()
            
            # Choose output format
            st.subheader("📁 Choose Output Format")
            
            format_col1, format_col2, format_col3 = st.columns(3)
            
            with format_col1:
                if st.button("🖼️ Generate PDF with Images", key="pdf_images"):
                    with st.spinner("Creating PDF with actual slide images..."):
                        pdf_bytes = create_image_combined_pdf(slides_list)
                        
                        if pdf_bytes:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            filename = f"team_slides_images_{timestamp}.pdf"
                            
                            st.download_button(
                                label="📥 Download PDF with Images",
                                data=pdf_bytes,
                                file_name=filename,
                                mime="application/pdf",
                                key="download_pdf_images"
                            )
                            st.success("✅ PDF with images generated successfully!")
                        else:
                            st.warning("Could not generate PDF with images. Try the simple version.")
            
            with format_col2:
                if st.button("📄 Generate Simple PDF", key="pdf_simple"):
                    with st.spinner("Creating simple PDF..."):
                        pdf_bytes = create_simple_combined_pdf(slides_list)
                        
                        if pdf_bytes:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            filename = f"team_slides_simple_{timestamp}.pdf"
                            
                            st.download_button(
                                label="📥 Download Simple PDF",
                                data=pdf_bytes,
                                file_name=filename,
                                mime="application/pdf",
                                key="download_pdf_simple"
                            )
                            st.success("✅ Simple PDF generated successfully!")
            
            with format_col3:
                if st.button("🌐 Generate HTML View", key="html_view"):
                    with st.spinner("Creating HTML view..."):
                        html_bytes = create_html_image_view(slides_list)
                        
                        if html_bytes:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            filename = f"team_slides_view_{timestamp}.html"
                            
                            st.download_button(
                                label="📥 Download HTML View",
                                data=html_bytes,
                                file_name=filename,
                                mime="text/html",
                                key="download_html"
                            )
                            st.success("✅ HTML view generated successfully!")
            
            st.divider()
            
            # Preview section
            st.subheader("👁️ Preview Presentations")
            
            # Show all presentations in expanders
            for slide in slides_list:
                with st.expander(f"📑 {slide.get('title', 'Untitled')} ({slide.get('slide_count', 0)} slides) - {slide.get('uploader', 'Unknown')}"):
                    col1, col2 = st.columns([1, 2])
                    
                    with col1:
                        st.write(f"**Uploader:** {slide.get('uploader', 'Unknown')}")
                        st.write(f"**Slides:** {slide.get('slide_count', 0)}")
                        st.write(f"**Uploaded:** {slide.get('upload_date', '')[:10]}")
                        if slide.get('description'):
                            st.write(f"**Description:** {slide.get('description')}")
                    
                    with col2:
                        # Embed first slide as preview
                        iframe = render_slide_in_streamlit(slide['presentation_id'])
                        st.markdown(iframe, unsafe_allow_html=True)
            
            st.divider()
            
            # Quick navigation back
            if st.button("← Back to Dashboard"):
                st.session_state.show_merged_view = False
                st.rerun()
    
    else:
        # Normal Dashboard View
        st.title("🎯 Google Slides Team Manager")
        
        if st.session_state.google_creds is None:
            st.warning("⚠️ Please connect your Google Account in the sidebar first!")
        
        # Create tabs
        tab1, tab2, tab3, tab4 = st.tabs(["📊 Combined Dashboard", "📤 Upload", "📋 My Uploads", "⚙️ Admin"])
        
        # Tab 1: Combined Dashboard
        with tab1:
            st.header("📊 Team Combined Slides Dashboard")
            
            # Refresh data
            refresh_shared_state()
            
            slides_list = st.session_state.shared_data['slides']
            
            if len(slides_list) == 0:
                st.info("No presentations uploaded yet. Upload slides to see them here!")
            else:
                # Show statistics
                total_slides = sum(slide.get('slide_count', 0) for slide in slides_list)
                all_uploaders = list(set(slide['uploader'] for slide in slides_list))
                
                # Stats
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("👥 Team Members", len(all_uploaders))
                with col2:
                    st.metric("📑 Presentations", len(slides_list))
                with col3:
                    st.metric("📄 Total Slides", total_slides)
                
                # Quick action buttons
                col_refresh, col_update, col_merged = st.columns(3)
                with col_refresh:
                    if st.button("🔄 Refresh", key="refresh_dash"):
                        refresh_shared_state()
                        st.success("Dashboard refreshed!")
                        st.rerun()
                
                with col_update:
                    if st.session_state.google_creds:
                        slides_service, _ = get_google_services()
                        if slides_service:
                            if st.button("🔄 Check Updates", key="check_updates"):
                                with st.spinner("Checking..."):
                                    updates = check_for_updates(slides_service)
                                    if updates:
                                        st.success("✅ Updates found!")
                                        st.rerun()
                                    else:
                                        st.info("✓ All up to date")
                        else:
                            st.write("")
                    else:
                        st.write("")
                
                with col_merged:
                    if st.button("📊 Generate Combined PDF/HTML"):
                        st.session_state.show_merged_view = True
                        st.rerun()
                
                st.divider()
                
                # Team contributions
                st.subheader("👥 Team Contributions")
                for uploader in all_uploaders:
                    user_slides = [s for s in slides_list if s['uploader'] == uploader]
                    user_slide_count = sum(s.get('slide_count', 0) for s in user_slides)
                    st.write(f"**{uploader}**: {len(user_slides)} presentation(s), {user_slide_count} slide(s)")
                
                st.divider()
                
                # All Team Slides
                st.subheader("📚 Team Presentations")
                
                # Sort slides by upload date (newest first)
                sorted_slides = sorted(slides_list, key=lambda x: x.get('upload_date', ''), reverse=True)
                
                # Display each presentation
                for idx, slide in enumerate(sorted_slides):
                    col1, col2, col3, col4 = st.columns([3, 1, 1, 2])
                    
                    with col1:
                        st.write(f"**{slide.get('title', 'Untitled')}**")
                        st.caption(f"Uploaded: {slide.get('upload_date', '')[:10]}")
                    
                    with col2:
                        st.write(f"📄 {slide.get('slide_count', 0)}")
                    
                    with col3:
                        if slide.get('uploader') == st.session_state.current_user:
                            st.success("👤 You")
                        else:
                            st.info(f"👥 {slide.get('uploader', 'Unknown')}")
                    
                    with col4:
                        if st.button(f"View", key=f"view_{slide['presentation_id']}"):
                            st.session_state.current_presentation_id = slide['presentation_id']
                            st.session_state.current_presentation_title = slide.get('title', 'Untitled')
                            st.session_state.current_presentation_uploader = slide.get('uploader', 'Unknown')
                            st.rerun()
                
                st.divider()
                
                # Display the selected presentation
                if 'current_presentation_id' in st.session_state:
                    current_slide = None
                    for slide in slides_list:
                        if slide['presentation_id'] == st.session_state.current_presentation_id:
                            current_slide = slide
                            break
                    
                    if current_slide:
                        st.subheader(f"📽️ {current_slide.get('title', 'Untitled')}")
                        st.write(f"**Uploaded by:** {current_slide.get('uploader', 'Unknown')} | **Slides:** {current_slide.get('slide_count', 0)}")
                        
                        # Embed the presentation
                        iframe = render_slide_in_streamlit(current_slide['presentation_id'])
                        st.markdown(iframe, unsafe_allow_html=True)
                        
                        # Quick download for this presentation
                      
                else:
                    # Show the most recent by default
                    if sorted_slides:
                        latest_slide = sorted_slides[0]
                        st.subheader(f"📽️ {latest_slide.get('title', 'Untitled')}")
                        st.write(f"**Uploaded by:** {latest_slide.get('uploader', 'Unknown')} | **Slides:** {latest_slide.get('slide_count', 0)}")
                        
                        iframe = render_slide_in_streamlit(latest_slide['presentation_id'])
                        st.markdown(iframe, unsafe_allow_html=True)
        
        # Tab 2: Upload Slides
        with tab2:
            st.header("📤 Upload New Presentation")
            
            if st.session_state.google_creds is None:
                st.warning("⚠️ Please connect your Google Account in the sidebar first!")
            else:
                slides_service, drive_service = get_google_services()
                
                with st.form("upload_form"):
                    st.info("📌 Your uploaded slides will appear in the Combined Dashboard for all team members to see")
                    
                    presentation_id = st.text_input(
                        "Google Slides Presentation ID *", 
                        help="Find in URL: docs.google.com/presentation/d/{THIS_PART}/edit",
                        placeholder="e.g., 1a2b3c4d5e6f7g8h9i0j"
                    )
                    
                    description = st.text_area("Description (optional)", 
                        placeholder="Brief description of your presentation...")
                    
                    submitted = st.form_submit_button("📤 Upload to Team Dashboard")
                    
                    if submitted:
                        if not presentation_id:
                            st.error("Please enter a presentation ID")
                        else:
                            with st.spinner("Fetching presentation details..."):
                                details = get_presentation_details(slides_service, presentation_id)
                                
                                if details:
                                    existing_ids = [s['presentation_id'] for s in st.session_state.shared_data['slides']]
                                    if presentation_id in existing_ids:
                                        st.warning("⚠️ Already in dashboard!")
                                        for i, slide in enumerate(st.session_state.shared_data['slides']):
                                            if slide['presentation_id'] == presentation_id:
                                                if slide['uploader'] == st.session_state.current_user or get_user_role(st.session_state.current_user) == 'admin':
                                                    st.session_state.shared_data['slides'][i].update({
                                                        'title': details['title'],
                                                        'slide_count': details['slide_count'],
                                                        'last_modified': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                        'description': description
                                                    })
                                                    save_shared_state()
                                                    log_activity("UPDATE", st.session_state.current_user, 
                                                               f"Updated '{details['title']}'")
                                                    st.success(f"✅ '{details['title']}' updated!")
                                                else:
                                                    st.error("❌ You can only update your own presentations")
                                                break
                                    else:
                                        new_slide = {
                                            'title': details['title'],
                                            'presentation_id': presentation_id,
                                            'presentation_link': f"https://docs.google.com/presentation/d/{presentation_id}/edit",
                                            'description': description,
                                            'uploader': st.session_state.current_user,
                                            'upload_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            'slide_count': details['slide_count'],
                                            'last_modified': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            'status': 'active'
                                        }
                                        
                                        st.session_state.shared_data['slides'].append(new_slide)
                                        save_shared_state()
                                        
                                        log_activity("UPLOAD", st.session_state.current_user, 
                                                   f"Uploaded '{details['title']}'")
                                        st.success(f"✅ '{details['title']}' uploaded!")
                                        st.balloons()
                                else:
                                    st.error("❌ Could not fetch presentation. Check sharing settings!")
                
                st.divider()
                st.markdown("""
                ### 📝 How to Upload:
                
                1. **Open your Google Slides** presentation
                2. **Share it**: Click 'Share' → 'Anyone with the link' → Viewer/Editor
                3. **Copy the Presentation ID** from URL: `docs.google.com/presentation/d/**{THIS_PART}**/edit`
                4. **Paste above** and upload!
                """)
        
        # Tab 3: My Uploads
        with tab3:
            st.header("📋 My Uploaded Slides")
            
            refresh_shared_state()
            
            my_slides = [s for s in st.session_state.shared_data['slides'] if s['uploader'] == st.session_state.current_user]
            
            if len(my_slides) == 0:
                st.info("No presentations uploaded yet.")
            else:
                st.success(f"You have {len(my_slides)} presentation(s)")
                
                for idx, slide in enumerate(my_slides):
                    with st.expander(f"📑 {slide.get('title', 'Untitled')} ({slide.get('slide_count', 'N/A')} slides)"):
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            st.write(f"**Uploaded:** {slide.get('upload_date', 'N/A')}")
                            st.write(f"**Slides:** {slide.get('slide_count', 'N/A')}")
                            st.write(f"**Last Modified:** {slide.get('last_modified', 'N/A')}")
                        
                        with col2:
                            st.write(f"**Description:** {slide.get('description', 'No description')}")
                            st.markdown(f"[🔗 Open in Google Slides]({slide.get('presentation_link', '#')})")
                        
                        col_a, col_b, col_c = st.columns(3)
                        
                        with col_a:
                            if st.button(f"🔄 Update", key=f"update_{idx}"):
                                if st.session_state.google_creds:
                                    slides_service, _ = get_google_services()
                                    if slides_service:
                                        with st.spinner("Checking..."):
                                            details = get_presentation_details(slides_service, slide['presentation_id'])
                                            if details:
                                                slide['slide_count'] = details['slide_count']
                                                slide['last_modified'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                                slide['title'] = details['title']
                                                save_shared_state()
                                                log_activity("MANUAL_UPDATE", st.session_state.current_user, 
                                                           f"Updated '{slide['title']}'")
                                                st.success("Updated!")
                                                st.rerun()
                        
                        with col_b:
                            if st.button(f"✏️ Edit", key=f"edit_{idx}"):
                                st.info("Edit in Google Slides")
                        
                        with col_c:
                            if st.button(f"🗑️ Delete", key=f"del_my_{idx}"):
                                for i, s in enumerate(st.session_state.shared_data['slides']):
                                    if s['presentation_id'] == slide['presentation_id']:
                                        st.session_state.shared_data['slides'].pop(i)
                                        save_shared_state()
                                        log_activity("DELETE", st.session_state.current_user, 
                                                   f"Deleted '{slide.get('title', 'Untitled')}'")
                                        st.success("Deleted!")
                                        st.rerun()
                                        break
                        
                        iframe = render_slide_in_streamlit(slide['presentation_id'])
                        st.markdown(iframe, unsafe_allow_html=True)
        
        # Tab 4: Admin Panel
        with tab4:
            # Check admin access using the improved function
            is_admin = check_admin_access()
            
            if not is_admin:
                st.warning("🔒 Admin access only")
                st.info(f"Your current role: {get_user_role(st.session_state.current_user)}")
                
                # Show instructions on how to get admin access
                with st.expander("ℹ️ How to get admin access?"):
                    st.markdown("""
                    ### Request Admin Access
                    
                    1. Contact an existing admin user
                    2. Ask them to change your role to "Admin" in the Admin Panel
                    3. Once changed, log out and log back in
                    4. You should now see the Admin Panel tab
                    
                    ### Current Admins:
                    """)
                    
                    # List current admins
                    admins = []
                    for username, data in st.session_state.shared_data['users'].items():
                        if data.get('role') == 'admin':
                            admins.append(username)
                    
                    if admins:
                        for admin in admins:
                            st.write(f"- **{admin}**")
                    else:
                        st.write("No admins found")
            else:
                st.header("⚙️ Admin Panel")
                st.success("👑 Admin Access Granted")
                
                refresh_shared_state()
                
                # Stats
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("👥 Users", len(st.session_state.shared_data['users']))
                with col2:
                    st.metric("📑 Presentations", len(st.session_state.shared_data['slides']))
                with col3:
                    admin_count = sum(1 for u in st.session_state.shared_data['users'].values() if u['role'] == 'admin')
                    st.metric("👑 Admins", admin_count)
                with col4:
                    st.metric("📝 Activities", len(st.session_state.shared_data['activities']))
                
                st.divider()
                
                # User Management
                st.subheader("👥 User Management")
                st.info("👑 Admin users can access the Admin Panel and manage users/presentations")

                refresh_shared_state()  # Force refresh to get latest user data

                # Sort users alphabetically
                sorted_users = sorted(st.session_state.shared_data['users'].items(), key=lambda x: x[0])

                for username, data in sorted_users:
                    col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
                    
                    with col1:
                        if username == st.session_state.current_user:
                            st.write(f"**{username}** 👈 (You)")
                        else:
                            st.write(f"**{username}**")
                    
                    with col2:
                        role = data.get('role', 'member')
                        if role == 'admin':
                            st.success("👑 Admin")
                        else:
                            st.info("👤 Member")
                    
                    with col3:
                        # Show last login/activity if available
                        if 'last_login' in data:
                            st.caption(f"Last: {data.get('last_login', '')[:10]}")
                        else:
                            st.caption("No activity")
                    
                    with col4:
                        if username != st.session_state.current_user:
                            current_role = data.get('role', 'member')
                            new_role = 'member' if current_role == 'admin' else 'admin'
                            
                            if st.button(f"Make {new_role}", key=f"role_{username}"):
                                # Update role in shared data
                                st.session_state.shared_data['users'][username]['role'] = new_role
                                save_shared_state()
                                
                                # Log the activity
                                log_activity("ROLE_CHANGE", st.session_state.current_user, 
                                           f"Changed {username} from {current_role} to {new_role}")
                                
                                # If we're changing our own role (in case admin demotes themselves),
                                # we need to update the session state
                                if username == st.session_state.current_user:
                                    st.session_state.current_user_role = new_role
                                
                                st.success(f"✅ {username} is now {new_role}!")
                                st.rerun()
                        else:
                            st.write("👤 Current user")
                
                st.divider()

                # Add a section to manually refresh user data
                st.subheader("🔄 Refresh User Data")

                col_refresh, col_export = st.columns(2)
                with col_refresh:
                    if st.button("🔄 Refresh All User Data"):
                        refresh_shared_state()
                        st.success("✅ User data refreshed!")
                        st.rerun()

                with col_export:
                    # Export users data
                    users_data = []
                    for username, data in st.session_state.shared_data['users'].items():
                        users_data.append({
                            'username': username,
                            'role': data.get('role', 'member'),
                            'has_google_auth': 'google_creds' in data if data else False
                        })
                    
                    users_json = json.dumps(users_data, indent=2)
                    st.download_button(
                        label="📥 Export Users List",
                        data=users_json,
                        file_name=f"users_export_{datetime.now().strftime('%Y%m%d')}.json",
                        mime="application/json"
                    )
                
                st.divider()
                
                # All Presentations Management
                st.subheader("📊 All Team Presentations")
                
                slides_list = st.session_state.shared_data['slides']
                
                for idx, slide in enumerate(slides_list):
                    col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
                    
                    with col1:
                        st.write(f"**{slide.get('title', 'Untitled')}**")
                        st.caption(f"by {slide.get('uploader', 'Unknown')}")
                    
                    with col2:
                        st.write(f"📄 {slide.get('slide_count', 0)} slides")
                    
                    with col3:
                        st.write(f"📅 {slide.get('upload_date', '')[:10]}")
                    
                    with col4:
                        if st.button(f"Remove", key=f"admin_remove_{slide['presentation_id']}"):
                            for i, s in enumerate(st.session_state.shared_data['slides']):
                                if s['presentation_id'] == slide['presentation_id']:
                                    st.session_state.shared_data['slides'].pop(i)
                                    save_shared_state()
                                    log_activity("ADMIN_DELETE", st.session_state.current_user, 
                                               f"Admin removed '{slide.get('title', 'Untitled')}'")
                                    st.success("Removed!")
                                    st.rerun()
                                    break
