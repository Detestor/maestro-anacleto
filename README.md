# MAESTRO ANACLETO (Render Web Service)

## Render start command
uvicorn anacleto_web:app --host 0.0.0.0 --port $PORT

## Env vars (Render -> Environment)
- TELEGRAM_TOKEN = <token bot>
- PUBLIC_BASE_URL = https://<tuo-servizio>.onrender.com
- WEBHOOK_PATH = /telegram  (opzionale)
- ALLOWED_GROUP_ID = -100... (opzionale)
- PDF_DIR = /opt/render/project/src/data/pdfs (opzionale; default corretto)

## Debug
- /debug/pdfs  -> lista file pdf visti su Render
- /debug/index -> stats indice globale
