# Render quick setup

**Type:** Web Service  
**Build command:** `pip install -r requirements.txt`  
**Start command:** `uvicorn anacleto_web:app --host 0.0.0.0 --port $PORT`

## Environment variables (Render dashboard -> Environment)

- `TELEGRAM_TOKEN` = <token bot>
- `PUBLIC_BASE_URL` = https://<tuo-servizio>.onrender.com   (senza slash finale)
- `WEBHOOK_PATH` = /telegram   (opzionale)
- `ALLOWED_GROUP_ID` = -1001950470064   (opzionale)
- `PDF_DIR` = /opt/render/project/src/data/pdfs   (opzionale; default già ok)

## Debug
- `/debug/pdfs` lista i pdf visti su Render
- `/debug/index` mostra lo stato dell'indice
- `/debug/reindex` forza rebuild indice
