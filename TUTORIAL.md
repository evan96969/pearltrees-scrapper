# 📥 Pearltrees File Downloader — Tutoriel d'utilisation

## Table des matières

1. [Prérequis](#1-prérequis)
2. [Installation](#2-installation)
3. [Trouver l'URL Pearltrees](#3-trouver-lurl-pearltrees)
4. [Utilisation de base](#4-utilisation-de-base)
5. [Options avancées](#5-options-avancées)
6. [Exemples de commandes](#6-exemples-de-commandes)
7. [Structure des fichiers téléchargés](#7-structure-des-fichiers-téléchargés)
8. [Dépannage](#8-dépannage)

---

## 1. Prérequis

| Requis | Version minimale | Obligatoire ? |
|--------|-----------------|---------------|
| Python | 3.10+           | ✅ Oui        |
| pip    | 21+             | ✅ Oui        |
| Chrome | dernière version| ⚠ Seulement si `--selenium` |

### Vérifier votre version Python

```bash
python --version
# Doit afficher Python 3.10.x ou supérieur
```

---

## 2. Installation

### Étape 1 — Cloner ou copier le projet

Placez les fichiers suivants dans un même dossier :

```
pearltrees-downloader/
├── pearltrees_downloader.py
├── requirements.txt
└── TUTORIAL.md        ← ce fichier
```

### Étape 2 — Créer un environnement virtuel (recommandé)

```bash
# Créer l'environnement
python -m venv venv

# Activer (Windows)
venv\Scripts\activate

# Activer (macOS / Linux)
source venv/bin/activate
```

### Étape 3 — Installer les dépendances

```bash
pip install -r requirements.txt
```

### Étape 4 — (Optionnel) Installer Chrome pour Selenium

Si vous souhaitez utiliser le mode `--selenium` (utile quand l'API ne répond pas) :

1. Installez [Google Chrome](https://www.google.com/chrome/)
2. Le driver sera téléchargé automatiquement par `webdriver-manager`

---

## 3. Trouver l'URL Pearltrees

### Comment récupérer l'URL d'une collection

1. Ouvrez **[pearltrees.com](https://www.pearltrees.com)** dans votre navigateur
2. Naviguez vers la collection qui vous intéresse
3. Copiez l'URL de la barre d'adresse

### Format attendu

L'URL doit ressembler à l'un de ces formats :

```
https://www.pearltrees.com/utilisateur/nom-collection/id12345678
https://www.pearltrees.com/utilisateur/id12345678
https://www.pearltrees.com/utilisateur#/N-id=12345678
```

> **💡 Astuce** : Le nombre à la fin (après `id`) est l'identifiant unique de la collection. C'est ce que le script utilise pour accéder aux données.

---

## 4. Utilisation de base

### Commande minimale

```bash
python pearltrees_downloader.py "https://www.pearltrees.com/utilisateur/collection/id12345678"
```

Cela va :
- Télécharger tous les fichiers de la page spécifiée
- Les ranger dans un dossier `downloads/` organisé par type
- Générer un `README.md` avec la liste des fichiers

### Voir l'aide

```bash
python pearltrees_downloader.py --help
```

---

## 5. Options avancées

| Option | Valeur | Description |
|--------|--------|-------------|
| `-o`, `--output` | chemin | Dossier de sortie (défaut : `downloads`) |
| `-d`, `--depth` | nombre | Profondeur de scraping des sous-collections (défaut : `0` = page actuelle) |
| `-r`, `--resume` | — | Reprendre un téléchargement interrompu |
| `--delay` | secondes | Délai entre les requêtes (défaut : `1.0`) |
| `--timeout` | secondes | Timeout par requête (défaut : `30`) |
| `--selenium` | — | Activer le fallback Selenium pour le contenu JS |
| `-m`, `--metadata` | `json` / `csv` / `both` | Exporter les métadonnées des fichiers |
| `-v`, `--verbose` | — | Mode debug (logs détaillés) |
| `--version` | — | Afficher la version |

### Profondeur de scraping

| Valeur | Comportement |
|--------|--------------|
| `0`    | Seulement la collection spécifiée (pas de sous-collections) |
| `1`    | La collection + ses sous-collections directes |
| `2`    | La collection + sous-collections + sous-sous-collections |
| `N`    | Récursif jusqu'à N niveaux de profondeur |

---

## 6. Exemples de commandes

### Télécharger une collection simple

```bash
python pearltrees_downloader.py "https://www.pearltrees.com/user/docs/id9876543"
```

### Télécharger avec les sous-collections (2 niveaux)

```bash
python pearltrees_downloader.py "https://www.pearltrees.com/user/docs/id9876543" --depth 2
```

### Reprendre un téléchargement interrompu

```bash
python pearltrees_downloader.py "https://www.pearltrees.com/user/docs/id9876543" --resume
```

### Exporter les métadonnées en JSON et CSV

```bash
python pearltrees_downloader.py "https://www.pearltrees.com/user/docs/id9876543" \
  --metadata both \
  --output mon_dossier
```

### Mode complet (récursif + reprise + métadonnées + verbose)

```bash
python pearltrees_downloader.py "https://www.pearltrees.com/user/docs/id9876543" \
  --depth 3 \
  --resume \
  --metadata json \
  --output export_complet \
  --delay 1.5 \
  --verbose
```

### Utiliser Selenium (si l'API ne répond pas)

```bash
python pearltrees_downloader.py "https://www.pearltrees.com/user/docs/id9876543" \
  --selenium \
  --verbose
```

---

## 7. Structure des fichiers téléchargés

Après exécution, votre dossier de sortie sera organisé comme suit :

```
downloads/
├── pdf/
│   ├── rapport_2024.pdf
│   └── guide_utilisateur.pdf
├── images/
│   ├── photo_equipe.jpg
│   └── diagramme.png
├── videos/
│   └── presentation.mp4
├── documents/
│   └── tableau.xlsx
├── audio/
│   └── podcast.mp3
├── archives/
│   └── donnees.zip
├── other/
│   └── fichier_inconnu.dat
├── README.md              ← rapport de téléchargement
├── metadata.json          ← (si --metadata json/both)
├── metadata.csv           ← (si --metadata csv/both)
└── pearltrees_download.log ← journal détaillé
```

---

## 8. Dépannage

### Erreur : « Impossible d'extraire le treeId »

**Cause** : L'URL n'est pas au bon format.

**Solution** : Vérifiez que l'URL contient bien un identifiant numérique (ex : `/id12345678`).

---

### Erreur : « URL bloquée par robots.txt »

**Cause** : L'URL cible est dans la zone `/s` (recherche) de Pearltrees.

**Solution** : Utilisez l'URL directe de la collection, pas une URL de recherche.

---

### Erreur : « Impossible de récupérer la collection »

**Cause** : L'API interne de Pearltrees a changé ou est temporairement indisponible.

**Solutions** :
1. Essayez avec `--selenium` pour utiliser le navigateur headless
2. Augmentez le `--delay` (ex : `--delay 3`)
3. Vérifiez que la collection est publique

---

### Les fichiers ne se téléchargent pas

**Causes possibles** :
- La collection ne contient que des liens (pas de fichiers uploadés)
- Les fichiers sont protégés par authentification

**Solution** : Activez le mode `--verbose` pour voir les logs détaillés.

---

### Erreur Selenium : « Impossible d'initialiser Selenium »

**Solutions** :
1. Installez Google Chrome
2. Vérifiez que `webdriver-manager` est bien installé : `pip install webdriver-manager`
3. Si vous êtes derrière un proxy, configurez les variables d'environnement

---

### Téléchargement interrompu (Ctrl+C)

Le script est conçu pour gérer les interruptions gracieusement :
- Les fichiers déjà téléchargés sont conservés
- Relancez avec `--resume` pour continuer

---

## Licence

Ce script est fourni « tel quel », sans garantie. Utilisez-le de manière responsable et respectez les conditions d'utilisation de Pearltrees.
