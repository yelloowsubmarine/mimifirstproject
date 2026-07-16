"""Outfit Finder — retrouve où acheter les vêtements d'une photo de tenue.

Pipeline :
  1. Claude (vision) détecte chaque pièce de la tenue + zone approximative sur l'image.
  2. Chaque pièce est recadrée puis uploadée sur un hébergeur temporaire (Litterbox, 1h).
  3. SerpApi (Google Lens) fait une recherche visuelle inversée sur chaque crop
     et renvoie des produits identiques/similaires avec liens d'achat et prix.
"""

import base64
import concurrent.futures
import io
import json
import os
from urllib.parse import quote_plus

import anthropic
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from PIL import Image

load_dotenv()

app = Flask(__name__)

SERPAPI_KEY = os.environ.get("SERPAPI_KEY")
LITTERBOX_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
MAX_ITEMS = 6          # nombre max de pièces analysées par photo
MAX_MATCHES = 8        # nombre max de résultats produits par pièce
CLAUDE_MODEL = "claude-opus-4-8"

ITEMS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nom court de la pièce en français, ex. « Blazer oversize beige »",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description précise : couleur, matière, coupe, détails distinctifs",
                    },
                    "brand_guess": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Marque probable si identifiable (logo, coupe signature), sinon null",
                    },
                    "search_query": {
                        "type": "string",
                        "description": "Requête de recherche shopping efficace pour retrouver cette pièce",
                    },
                    "box": {
                        "type": "object",
                        "description": "Zone de la pièce sur l'image, en pourcentage des dimensions (0-100)",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "w": {"type": "number"},
                            "h": {"type": "number"},
                        },
                        "required": ["x", "y", "w", "h"],
                        "additionalProperties": False,
                    },
                },
                "required": ["name", "description", "brand_guess", "search_query", "box"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

ANALYZE_PROMPT = f"""Analyse cette photo de tenue (mode / streetwear / éditorial).

Identifie chaque pièce visible portée : vêtements, chaussures, sacs, accessoires notables
(lunettes, ceinture, bijoux marquants). Maximum {MAX_ITEMS} pièces, les plus identifiables d'abord.

Pour chaque pièce :
- name : nom court en français
- description : couleur exacte, matière, coupe, détails distinctifs (boutons, coutures, motifs)
- brand_guess : la marque si tu la reconnais (logo, design signature), sinon null
- search_query : la requête qu'une personne taperait sur Google Shopping pour retrouver
  cette pièce précise (inclus la marque si identifiée)
- box : la zone approximative de la pièce sur l'image en pourcentage (x, y = coin haut-gauche,
  w, h = largeur/hauteur). Sois généreux sur la zone pour ne pas couper la pièce.

Ignore les éléments de décor et les vêtements d'autres personnes en arrière-plan."""


def get_claude_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)


def prepare_image(file_storage):
    """Ouvre l'image uploadée, la normalise en JPEG RGB (max 2000px)."""
    img = Image.open(file_storage.stream)
    img = img.convert("RGB")
    img.thumbnail((2000, 2000))
    return img


def image_to_base64_jpeg(img, quality=88):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def detect_items(client, image_b64):
    """Demande à Claude la liste structurée des pièces de la tenue."""
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        output_config={"format": {"type": "json_schema", "schema": ITEMS_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": ANALYZE_PROMPT},
                ],
            }
        ],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("L'analyse de l'image a été refusée par le modèle.")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["items"][:MAX_ITEMS]


def crop_item(img, box, margin=0.10):
    """Recadre une pièce à partir de sa box en %, avec une marge de sécurité."""
    W, H = img.size
    x = box["x"] / 100 * W
    y = box["y"] / 100 * H
    w = box["w"] / 100 * W
    h = box["h"] / 100 * H
    mx, my = w * margin, h * margin
    left = max(0, int(x - mx))
    top = max(0, int(y - my))
    right = min(W, int(x + w + mx))
    bottom = min(H, int(y + h + my))
    if right - left < 40 or bottom - top < 40:
        return img.copy()  # box aberrante : on garde l'image entière
    crop = img.crop((left, top, right, bottom))
    crop.thumbnail((900, 900))
    return crop


def upload_temp(crop):
    """Uploade le crop sur Litterbox (hébergement anonyme, expire après 1h).

    Google Lens (via SerpApi) exige une URL d'image publique — l'image recadrée
    est donc accessible publiquement pendant 1h, puis supprimée.
    """
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    resp = requests.post(
        LITTERBOX_URL,
        data={"reqtype": "fileupload", "time": "1h"},
        files={"fileToUpload": ("item.jpg", buf, "image/jpeg")},
        timeout=30,
    )
    resp.raise_for_status()
    url = resp.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"Upload temporaire échoué : {url[:200]}")
    return url


def lens_search(image_url):
    """Recherche visuelle inversée Google Lens via SerpApi."""
    resp = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": "google_lens",
            "url": image_url,
            "api_key": SERPAPI_KEY,
            "hl": "fr",
            "country": "fr",
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    matches = []
    for m in data.get("visual_matches", []):
        price = m.get("price") or {}
        matches.append(
            {
                "title": m.get("title"),
                "link": m.get("link"),
                "source": m.get("source"),
                "price": price.get("value"),
                "thumbnail": m.get("thumbnail"),
            }
        )
        if len(matches) >= MAX_MATCHES:
            break
    # Les résultats avec prix (pages produit) d'abord
    matches.sort(key=lambda m: m["price"] is None)
    return matches


def search_links(query):
    q = quote_plus(query)
    return {
        "google_shopping": f"https://www.google.com/search?tbm=shop&q={q}",
        "vinted": f"https://www.vinted.fr/catalog?search_text={q}",
        "google_images": f"https://www.google.com/search?tbm=isch&q={q}",
    }


def process_item(img, item):
    """Crop + upload + recherche Lens pour une pièce. Tolérant aux échecs."""
    crop = crop_item(img, item["box"])
    crop_b64 = image_to_base64_jpeg(crop, quality=80)
    result = {
        "name": item["name"],
        "description": item["description"],
        "brand_guess": item["brand_guess"],
        "crop": f"data:image/jpeg;base64,{crop_b64}",
        "matches": [],
        "links": search_links(item["search_query"]),
        "error": None,
    }
    if not SERPAPI_KEY:
        result["error"] = "SERPAPI_KEY absente : recherche visuelle désactivée."
        return result
    try:
        url = upload_temp(crop)
        result["matches"] = lens_search(url)
    except Exception as e:
        result["error"] = f"Recherche visuelle échouée : {e}"
    return result


@app.route("/")
def index():
    return render_template(
        "index.html",
        serpapi_enabled=bool(SERPAPI_KEY),
        anthropic_enabled=bool(os.environ.get("ANTHROPIC_API_KEY")),
    )


@app.route("/api/analyze", methods=["POST"])
def analyze():
    client = get_claude_client()
    if client is None:
        return jsonify({"error": "ANTHROPIC_API_KEY manquante (voir README)."}), 500
    if "photo" not in request.files:
        return jsonify({"error": "Aucune image reçue."}), 400

    try:
        img = prepare_image(request.files["photo"])
    except Exception:
        return jsonify({"error": "Image illisible. Formats acceptés : JPEG, PNG, WebP."}), 400

    try:
        items = detect_items(client, image_to_base64_jpeg(img))
    except anthropic.APIStatusError as e:
        return jsonify({"error": f"Erreur API Claude ({e.status_code}) : {e.message}"}), 502
    except Exception as e:
        return jsonify({"error": f"Analyse impossible : {e}"}), 500

    if not items:
        return jsonify({"error": "Aucun vêtement détecté sur cette photo."}), 422

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda it: process_item(img, it), items))

    return jsonify({"items": results})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
