# Google Slides Team Manager

A Streamlit application for team collaboration on Google Slides presentations.

## Features
- 📤 Upload Google Slides presentations
- 👁️ All team slides combined in one dashboard
- 🔄 Real-time updates
- 👥 Team collaboration
- 📊 Merged PDF with actual slide images
- 📥 Download combined files

## Setup for Development

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Set up Google Cloud OAuth credentials
4. Update `.streamlit/secrets.toml` with your credentials
5. Run: `streamlit run app.py`

## Deployment to Streamlit Cloud

1. Push to GitHub repository
2. Connect to Streamlit Cloud
3. Add secrets in Streamlit Cloud dashboard
4. Deploy!

## Environment Variables
- `GOOGLE_CLIENT_ID`: Google OAuth Client ID
- `GOOGLE_CLIENT_SECRET`: Google OAuth Client Secret