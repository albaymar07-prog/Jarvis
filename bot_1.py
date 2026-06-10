import os
import json
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from duckduckgo_search import DDGS

# ── Configuración ──────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_KEY  = os.environ["OPENROUTER_API_KEY"]
MODEL           = "meta-llama/llama-3.3-70b-instruct:free"
MAX_HISTORY     = 20   # mensajes a recordar por usuario

# Memoria en RAM (se guarda mientras el servicio está activo)
conversations: dict[str, list] = {}

# ── Personalidad ───────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Eres Jarvis, un asistente personal inteligente y cercano.
Hablas de forma natural como un amigo que lo sabe todo, no como un robot.

REGLAS:
- Responde siempre en el idioma del usuario (por defecto español)
- Sé conciso y directo en conversación, detallado cuando se pide información
- Usa emojis con moderación 😊
- Usa *negrita* para énfasis en Telegram
- Cuando necesites datos actuales (noticias, precios, eventos), avisa que buscas y usa la función de búsqueda
- Recuerda lo que el usuario te ha contado en la conversación
"""

# ── Búsqueda web con DuckDuckGo (gratis, sin API key) ─────────────────────────
def buscar_web(query: str) -> str:
    try:
        with DDGS() as ddgs:
            resultados = list(ddgs.text(query, max_results=5))
        if not resultados:
            return "No encontré resultados para esa búsqueda."
        texto = f"Resultados web para '{query}':\n\n"
        for i, r in enumerate(resultados, 1):
            texto += f"{i}. {r.get('title', '')}\n{r.get('body', '')[:300]}\n\n"
        return texto
    except Exception as e:
        return f"Error al buscar: {e}"

# ── Llamada a OpenRouter ───────────────────────────────────────────────────────
def llamar_ia(historial: list, mensaje_usuario: str) -> str:
    mensajes = [{"role": "system", "content": SYSTEM_PROMPT}]
    mensajes += historial
    mensajes.append({"role": "user", "content": mensaje_usuario})

    # Detectar si el mensaje requiere búsqueda web
    palabras_busqueda = ["busca", "buscar", "qué es", "qué fue", "cuándo", "precio",
                         "hoy", "ahora", "noticias", "última", "último", "reciente"]
    necesita_busqueda = any(p in mensaje_usuario.lower() for p in palabras_busqueda)

    contexto_web = ""
    if necesita_busqueda:
        contexto_web = buscar_web(mensaje_usuario)
        mensajes.append({
            "role": "system",
            "content": f"Información encontrada en internet:\n{contexto_web}"
        })

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://jarvis-bot.app",
                "X-Title": "Jarvis Telegram Bot",
            },
            json={"model": MODEL, "messages": mensajes, "max_tokens": 1000},
            timeout=30,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Error OpenRouter: {e}")
        return f"❌ Error al conectar con la IA: {e}"

# ── Handlers de Telegram ───────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.effective_user.first_name or "amigo"
    await update.message.reply_text(
        f"¡Hola {nombre}! 👋 Soy *Jarvis*, tu asistente personal.\n\n"
        "Puedo ayudarte con:\n"
        "• 🔍 Buscar información actualizada en internet\n"
        "• 💬 Conversar sobre cualquier tema\n"
        "• ✍️ Redactar, traducir, resumir textos\n"
        "• 🧮 Cálculos, análisis, código\n\n"
        "Escríbeme lo que necesites 😊\n"
        "_/limpiar para borrar el historial_",
        parse_mode="Markdown",
    )

async def cmd_limpiar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    conversations[uid] = []
    await update.message.reply_text("🗑️ Historial borrado. ¡Empezamos de cero!")

async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Comandos:*\n"
        "/start — Bienvenida\n"
        "/limpiar — Borrar historial\n"
        "/ayuda — Esta ayuda\n\n"
        "Simplemente escríbeme en lenguaje natural 👇",
        parse_mode="Markdown",
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = str(update.effective_user.id)
    texto   = update.message.text
    nombre  = update.effective_user.first_name

    logger.info(f"📩 {nombre}: {texto[:60]}")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    historial = conversations.get(uid, [])
    respuesta = llamar_ia(historial, texto)

    # Actualizar historial
    historial.append({"role": "user",      "content": texto})
    historial.append({"role": "assistant", "content": respuesta})
    if len(historial) > MAX_HISTORY * 2:
        historial = historial[-(MAX_HISTORY * 2):]
    conversations[uid] = historial

    # Enviar respuesta (dividir si es muy larga)
    for i in range(0, len(respuesta), 4096):
        await update.message.reply_text(
            respuesta[i:i+4096],
            parse_mode="Markdown",
        )

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("limpiar", cmd_limpiar))
    app.add_handler(CommandHandler("clear",   cmd_limpiar))
    app.add_handler(CommandHandler("ayuda",   cmd_ayuda))
    app.add_handler(CommandHandler("help",    cmd_ayuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ Jarvis iniciado y escuchando...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
