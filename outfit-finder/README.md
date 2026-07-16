# Outfit Finder 👗🔎

Petite appli web perso : tu déposes une photo de tenue (Instagram, Pinterest, screenshot…),
l'IA identifie chaque pièce (haut, pantalon, chaussures, sac, accessoires) et cherche
où l'acheter, avec liens et prix.

## Comment ça marche

1. **Claude (vision)** analyse la photo : liste des pièces, description précise
   (couleur, matière, coupe), marque probable, et zone de chaque pièce sur l'image.
2. Chaque pièce est **recadrée** automatiquement, puis uploadée sur un hébergeur
   d'images temporaire ([Litterbox](https://litterbox.catbox.moe), expiration 1h) —
   nécessaire car Google Lens exige une URL d'image publique.
3. **SerpApi (Google Lens)** fait une recherche visuelle inversée sur chaque pièce
   et renvoie des produits identiques ou similaires avec lien d'achat et prix.
4. En complément, des liens de recherche pré-remplis (Google Shopping, Vinted) sont
   générés à partir de la description de chaque pièce.

## Installation

```bash
cd outfit-finder
python3 -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration des clés

```bash
cp .env.example .env
```

Puis édite `.env` :

| Clé | Où l'obtenir | Coût |
|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/settings/keys) | ~0,05–0,15 € par photo analysée |
| `SERPAPI_KEY` | [serpapi.com](https://serpapi.com/manage-api-key) (inscription gratuite) | ~100 recherches/mois gratuites (1 recherche = 1 pièce détectée) |

Sans `SERPAPI_KEY`, l'appli fonctionne quand même mais ne propose que les liens de
recherche (pas de recherche visuelle exacte).

## Lancer l'appli

```bash
python app.py
```

Puis ouvre **http://localhost:5000** — glisse une photo, clique sur
« Trouver les vêtements », et patiente 30 s à 1 min.

## Limites et remarques

- **Meilleurs résultats** : vêtements de marques/enseignes connues, photos nettes,
  pièces bien visibles. Pour du vintage ou des créateurs indépendants, tu obtiendras
  surtout des articles *similaires*.
- **Confidentialité** : les zones recadrées de la photo sont hébergées publiquement
  pendant 1 heure sur Litterbox (le temps que Google Lens les analyse), puis supprimées
  automatiquement. Évite les photos que tu ne voudrais pas voir circuler.
- **Quota SerpApi** : chaque pièce détectée consomme 1 recherche. Une photo avec 5 pièces
  = 5 recherches (~20 photos/mois avec le quota gratuit).
