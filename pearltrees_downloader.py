#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pearltrees File Downloader
===========================
Script robuste pour télécharger tous les fichiers (PDF, images, vidéos, etc.)
d'une page Pearltrees donnée, avec gestion récursive des sous-collections.

Auteur : Assistant IA
Version : 1.0.0
Python  : 3.10+
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import mimetypes
import os
import re
import sys
import time
import urllib.robotparser
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION = "1.0.0"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
PEARLTREES_BASE = "https://www.pearltrees.com"
API_TREE_ENDPOINT = "/s/treeandpearlsapi/getTreeAndPearls"
API_DETAIL_ENDPOINT = "/s/readerapi/getDetailForPearl"

# File type classification by MIME prefix / extension
FILE_CATEGORIES: dict[str, list[str]] = {
    "pdf": [".pdf"],
    "images": [
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp",
        ".tiff", ".tif", ".ico", ".heic", ".heif", ".avif",
    ],
    "videos": [
        ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm",
        ".m4v", ".mpeg", ".mpg", ".3gp",
    ],
    "audio": [
        ".mp3", ".wav", ".ogg", ".flac", ".aac", ".wma", ".m4a", ".opus",
    ],
    "documents": [
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".odt", ".ods", ".odp", ".rtf", ".txt", ".csv", ".epub",
    ],
    "archives": [
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
    ],
}

DEFAULT_DELAY = 1.0  # secondes entre les requêtes
DEFAULT_TIMEOUT = 30  # secondes
MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logger = logging.getLogger("pearltrees_downloader")


def setup_logging(log_file: str = "pearltrees_download.log", verbose: bool = False) -> None:
    """Configure le logging vers console et fichier."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)-8s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logger.setLevel(logging.DEBUG)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logger.addHandler(ch)

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logger.addHandler(fh)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PearlItem:
    """Représente un élément (perle) dans une collection Pearltrees."""
    pearl_id: str = ""
    title: str = ""
    url: str = ""
    description: str = ""
    pearl_type: str = ""          # "link", "file", "note", "collection"
    content_type: str = ""         # MIME type si connu
    tags: list[str] = field(default_factory=list)
    thumbnail_url: str = ""
    parent_collection: str = ""
    ref_tree_id: str = ""          # ID de sous-collection si c'est un dossier
    local_path: str = ""           # chemin local après téléchargement
    download_status: str = "pending"  # pending, downloaded, skipped, error


# ---------------------------------------------------------------------------
# Robots.txt Checker
# ---------------------------------------------------------------------------

class RobotsChecker:
    """Vérifie la conformité robots.txt avant de scraper."""

    def __init__(self, base_url: str = PEARLTREES_BASE):
        self.base_url = base_url
        self._parser = urllib.robotparser.RobotFileParser()
        self._loaded = False

    def load(self) -> None:
        """Charge et parse le fichier robots.txt."""
        robots_url = f"{self.base_url}/robots.txt"
        try:
            self._parser.set_url(robots_url)
            self._parser.read()
            self._loaded = True
            logger.info("robots.txt chargé depuis %s", robots_url)
        except Exception as e:
            logger.warning("Impossible de charger robots.txt : %s", e)
            self._loaded = False

    def can_fetch(self, url: str) -> bool:
        """Vérifie si l'URL est autorisée par robots.txt."""
        if not self._loaded:
            self.load()
        allowed = self._parser.can_fetch(USER_AGENT, url)
        if not allowed:
            logger.warning("URL bloquée par robots.txt : %s", url)
        return allowed


# ---------------------------------------------------------------------------
# Pearltrees API Client
# ---------------------------------------------------------------------------

class PearltreesAPI:
    """
    Client pour l'API interne de Pearltrees.
    Utilise requests pour les appels API JSON.
    Fallback Selenium si l'API ne répond pas.
    """

    def __init__(
        self,
        delay: float = DEFAULT_DELAY,
        timeout: int = DEFAULT_TIMEOUT,
        use_selenium: bool = False,
    ):
        self.delay = delay
        self.timeout = timeout
        self.use_selenium = use_selenium
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": PEARLTREES_BASE,
        })
        self._driver = None
        self._last_request_time = 0.0

    def _rate_limit(self) -> None:
        """Respecte le délai entre les requêtes."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.delay:
            sleep_time = self.delay - elapsed
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _get_json(self, url: str, params: dict | None = None) -> dict | list | None:
        """Effectue un GET et retourne le JSON parsé, avec retry."""
        for attempt in range(1, MAX_RETRIES + 1):
            self._rate_limit()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.HTTPError as e:
                logger.warning("HTTP %s pour %s (tentative %d/%d)",
                               resp.status_code, url, attempt, MAX_RETRIES)
                if resp.status_code == 429:
                    wait = min(2 ** attempt * 5, 60)
                    logger.info("Rate limited — attente de %ds…", wait)
                    time.sleep(wait)
                elif resp.status_code >= 500:
                    time.sleep(2 ** attempt)
                else:
                    break
            except requests.exceptions.ConnectionError as e:
                logger.warning("Erreur de connexion : %s (tentative %d/%d)",
                               e, attempt, MAX_RETRIES)
                time.sleep(2 ** attempt)
            except requests.exceptions.Timeout:
                logger.warning("Timeout pour %s (tentative %d/%d)",
                               url, attempt, MAX_RETRIES)
                time.sleep(2 ** attempt)
            except (json.JSONDecodeError, ValueError):
                logger.warning("Réponse non-JSON pour %s", url)
                return None
        return None

    def _get_html(self, url: str) -> str | None:
        """Effectue un GET et retourne le HTML brut."""
        for attempt in range(1, MAX_RETRIES + 1):
            self._rate_limit()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                return resp.text
            except Exception as e:
                logger.warning("Erreur HTML pour %s : %s (tentative %d/%d)",
                               url, e, attempt, MAX_RETRIES)
                time.sleep(2 ** attempt)
        return None

    # ----- Selenium Fallback -----

    def _init_selenium(self) -> None:
        """Initialise le driver Selenium (Chrome headless)."""
        if self._driver is not None:
            return
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager

            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument(f"--user-agent={USER_AGENT}")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")

            service = Service(ChromeDriverManager().install())
            self._driver = webdriver.Chrome(service=service, options=options)
            self._driver.set_page_load_timeout(self.timeout)
            logger.info("Selenium (Chrome headless) initialisé")
        except Exception as e:
            logger.error("Impossible d'initialiser Selenium : %s", e)
            self._driver = None

    def _selenium_get_page_source(self, url: str, wait: int = 5) -> str | None:
        """Charge une page avec Selenium et retourne le source."""
        self._init_selenium()
        if self._driver is None:
            return None
        try:
            self._rate_limit()
            self._driver.get(url)
            time.sleep(wait)  # attendre le rendu JS
            return self._driver.page_source
        except Exception as e:
            logger.error("Erreur Selenium pour %s : %s", url, e)
            return None

    def _selenium_get_api_data(self, tree_id: str) -> dict | None:
        """
        Utilise Selenium pour naviguer vers la page de la collection
        et intercepter les données API via JavaScript.
        """
        self._init_selenium()
        if self._driver is None:
            return None
        try:
            self._rate_limit()
            url = f"{PEARLTREES_BASE}/s/treeandpearlsapi/getTreeAndPearls?treeId={tree_id}"
            self._driver.get(url)
            time.sleep(3)
            page_text = self._driver.find_element("tag name", "body").text
            if page_text:
                return json.loads(page_text)
        except Exception as e:
            logger.debug("Selenium API fallback échoué : %s", e)
        return None

    def close(self) -> None:
        """Ferme le driver Selenium s'il est ouvert."""
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    # ----- Public API methods -----

    @staticmethod
    def extract_tree_id(url: str) -> str | None:
        """
        Extrait le treeId depuis une URL Pearltrees.
        Formats supportés :
          - https://www.pearltrees.com/user/collection/id12345
          - https://www.pearltrees.com/user/collection/id12345/
          - https://www.pearltrees.com/user#/N-id=12345...
        """
        # Format standard : /idNNNNN à la fin de l'URL
        match = re.search(r"/id(\d+)", url)
        if match:
            return match.group(1)

        # Format hash : N-id=NNNNN
        match = re.search(r"N-id=(\d+)", url)
        if match:
            return match.group(1)

        # Tenter d'extraire tout nombre à la fin
        match = re.search(r"/(\d{5,})/?$", url)
        if match:
            return match.group(1)

        return None

    def get_collection(self, tree_id: str) -> dict | None:
        """Récupère les données d'une collection via l'API interne."""
        url = f"{PEARLTREES_BASE}{API_TREE_ENDPOINT}"
        params = {"treeId": tree_id}
        logger.info("Récupération de la collection treeId=%s …", tree_id)

        data = self._get_json(url, params)

        # Fallback : essayer Selenium si l'API échoue
        if data is None and self.use_selenium:
            logger.info("Fallback Selenium pour treeId=%s", tree_id)
            data = self._selenium_get_api_data(tree_id)

        if data is None:
            logger.error("Impossible de récupérer la collection treeId=%s", tree_id)
        return data

    def get_pearl_detail(self, pearl_id: str) -> dict | None:
        """Récupère les détails d'une perle individuelle."""
        url = f"{PEARLTREES_BASE}{API_DETAIL_ENDPOINT}"
        params = {"pearlId": pearl_id}
        return self._get_json(url, params)

    def get_page_pearls_html(self, page_url: str) -> list[PearlItem]:
        """
        Fallback HTML : parse la page Pearltrees pour en extraire les
        liens et fichiers via BeautifulSoup (si l'API ne fonctionne pas).
        """
        html = None
        if self.use_selenium:
            html = self._selenium_get_page_source(page_url, wait=8)
        if html is None:
            html = self._get_html(page_url)
        if html is None:
            return []

        soup = BeautifulSoup(html, "html.parser")
        items: list[PearlItem] = []

        # Chercher des liens vers des fichiers
        for link in soup.find_all("a", href=True):
            href = link["href"]
            abs_url = urljoin(page_url, href)
            if _is_downloadable_url(abs_url):
                title = link.get_text(strip=True) or _filename_from_url(abs_url)
                items.append(PearlItem(
                    title=title,
                    url=abs_url,
                    pearl_type="link",
                ))

        # Chercher des données JSON embarquées
        for script in soup.find_all("script"):
            if script.string and "pearls" in (script.string or ""):
                try:
                    match = re.search(r'\{.*"pearls".*\}', script.string, re.DOTALL)
                    if match:
                        data = json.loads(match.group())
                        items.extend(self._parse_pearls_from_json(data))
                except (json.JSONDecodeError, AttributeError):
                    pass

        return items

    def _parse_pearls_from_json(self, data: dict) -> list[PearlItem]:
        """Parse les pearls depuis une réponse JSON de l'API."""
        items: list[PearlItem] = []
        pearls = []

        # L'API peut retourner différentes structures
        if isinstance(data, dict):
            pearls = data.get("pearls", [])
            if not pearls:
                pearls = data.get("entries", [])
            if not pearls and "tree" in data:
                pearls = data["tree"].get("pearls", [])
        elif isinstance(data, list):
            pearls = data

        for pearl in pearls:
            if not isinstance(pearl, dict):
                continue

            item = PearlItem()
            item.pearl_id = str(pearl.get("pearlId", pearl.get("id", "")))
            item.title = pearl.get("title", pearl.get("name", ""))
            item.description = pearl.get("description", pearl.get("excerpt", ""))

            # Extraire l'URL du fichier
            url_data = pearl.get("url")
            if isinstance(url_data, dict):
                item.url = url_data.get("url", "")
                item.content_type = url_data.get("contentType", "")
            elif isinstance(url_data, str):
                item.url = url_data
            else:
                item.url = pearl.get("urlFile", pearl.get("sourceUrl", ""))

            # Si pas d'URL directe, chercher dans d'autres champs
            if not item.url:
                item.url = pearl.get("originalURL", pearl.get("source", ""))

            # Thumbnail
            item.thumbnail_url = pearl.get("thumbnailUrl",
                                           pearl.get("thumbUrl",
                                                      pearl.get("thumbnail", "")))

            # Type de perle
            pearl_type_raw = str(pearl.get("type", pearl.get("pearlType", ""))).lower()
            if pearl_type_raw in ("reference", "ref", "collection", "folder"):
                item.pearl_type = "collection"
                item.ref_tree_id = str(pearl.get("refTreeId",
                                                  pearl.get("treeId",
                                                             pearl.get("objectId", ""))))
            elif pearl_type_raw in ("page", "link", "url"):
                item.pearl_type = "link"
            elif pearl_type_raw in ("file", "upload", "document"):
                item.pearl_type = "file"
            elif pearl_type_raw in ("note", "text"):
                item.pearl_type = "note"
            else:
                item.pearl_type = pearl_type_raw or "link"

            # Tags
            tags = pearl.get("tags", pearl.get("keywords", []))
            if isinstance(tags, list):
                item.tags = [str(t) for t in tags]
            elif isinstance(tags, str):
                item.tags = [t.strip() for t in tags.split(",") if t.strip()]

            if item.url or item.pearl_type == "collection":
                items.append(item)

        return items


# ---------------------------------------------------------------------------
# File Downloader
# ---------------------------------------------------------------------------

class FileDownloader:
    """Télécharge des fichiers avec barre de progression et reprise."""

    def __init__(
        self,
        output_dir: str = "downloads",
        resume: bool = False,
        delay: float = DEFAULT_DELAY,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.output_dir = Path(output_dir)
        self.resume = resume
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        })
        self._last_request_time = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_time = time.time()

    def download_file(self, url: str, category: str = "", filename: str = "") -> Path | None:
        """
        Télécharge un fichier et le range dans le bon dossier.

        Args:
            url: URL du fichier à télécharger
            category: Catégorie (pdf, images, etc.) — auto-détectée si vide
            filename: Nom du fichier — extrait de l'URL si vide

        Returns:
            Le chemin local du fichier téléchargé, ou None si échec
        """
        if not url:
            return None

        # Déduire le nom du fichier
        if not filename:
            filename = _filename_from_url(url)

        # Auto-détecter la catégorie
        if not category:
            category = _categorize_file(filename)

        # Créer le dossier cible
        target_dir = self.output_dir / category
        target_dir.mkdir(parents=True, exist_ok=True)

        # Nettoyer le nom de fichier
        filename = _sanitize_filename(filename)
        target_path = target_dir / filename

        # Gestion de la reprise
        if target_path.exists() and self.resume:
            existing_size = target_path.stat().st_size
            if existing_size > 0:
                logger.info("⏭  Fichier déjà téléchargé (reprise) : %s", filename)
                return target_path
        elif target_path.exists():
            # Ajouter un suffixe pour ne pas écraser
            stem = target_path.stem
            suffix = target_path.suffix
            counter = 1
            while target_path.exists():
                target_path = target_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        # Téléchargement avec retry
        for attempt in range(1, MAX_RETRIES + 1):
            self._rate_limit()
            try:
                resp = self.session.get(url, stream=True, timeout=self.timeout)
                resp.raise_for_status()

                total_size = int(resp.headers.get("content-length", 0))
                block_size = 8192

                with open(target_path, "wb") as f, tqdm(
                    total=total_size,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=f"  📥 {filename[:50]}",
                    leave=False,
                    ncols=80,
                ) as pbar:
                    for chunk in resp.iter_content(chunk_size=block_size):
                        if chunk:
                            f.write(chunk)
                            pbar.update(len(chunk))

                logger.info("✅ Téléchargé : %s → %s", filename, target_path)
                return target_path

            except requests.exceptions.HTTPError as e:
                status = resp.status_code if resp else "?"
                logger.warning("HTTP %s pour %s (tentative %d/%d)",
                               status, url, attempt, MAX_RETRIES)
                if status == 429:
                    time.sleep(min(2 ** attempt * 5, 60))
                elif status == 404:
                    logger.error("❌ Fichier introuvable (404) : %s", url)
                    return None
                else:
                    time.sleep(2 ** attempt)
            except requests.exceptions.ConnectionError:
                logger.warning("Erreur de connexion pour %s (tentative %d/%d)",
                               url, attempt, MAX_RETRIES)
                time.sleep(2 ** attempt)
            except requests.exceptions.Timeout:
                logger.warning("Timeout pour %s (tentative %d/%d)",
                               url, attempt, MAX_RETRIES)
                time.sleep(2 ** attempt)
            except Exception as e:
                logger.error("Erreur inattendue pour %s : %s", url, e)
                return None

        logger.error("❌ Échec du téléchargement après %d tentatives : %s",
                      MAX_RETRIES, url)
        return None


# ---------------------------------------------------------------------------
# Metadata Exporter
# ---------------------------------------------------------------------------

class MetadataExporter:
    """Exporte les métadonnées des fichiers téléchargés en JSON ou CSV."""

    @staticmethod
    def export_json(items: list[PearlItem], output_path: Path) -> None:
        """Exporte en JSON."""
        data = []
        for item in items:
            entry = asdict(item)
            data.append(entry)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("📋 Métadonnées exportées en JSON : %s", output_path)

    @staticmethod
    def export_csv(items: list[PearlItem], output_path: Path) -> None:
        """Exporte en CSV."""
        if not items:
            return

        fieldnames = [
            "pearl_id", "title", "url", "description", "pearl_type",
            "content_type", "tags", "parent_collection", "local_path",
            "download_status",
        ]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for item in items:
                row = asdict(item)
                row["tags"] = ", ".join(row.get("tags", []))
                writer.writerow(row)
        logger.info("📋 Métadonnées exportées en CSV : %s", output_path)


# ---------------------------------------------------------------------------
# Main Scraper / Orchestrator
# ---------------------------------------------------------------------------

class PearltreesScraper:
    """
    Orchestrateur principal : récupère les collections,
    télécharge les fichiers, et exporte les résultats.
    """

    def __init__(
        self,
        url: str,
        output_dir: str = "downloads",
        depth: int = 0,
        resume: bool = False,
        delay: float = DEFAULT_DELAY,
        timeout: int = DEFAULT_TIMEOUT,
        use_selenium: bool = False,
        metadata_format: str | None = None,
    ):
        self.original_url = url
        self.output_dir = Path(output_dir)
        self.max_depth = depth
        self.resume = resume
        self.metadata_format = metadata_format

        self.robots = RobotsChecker()
        self.api = PearltreesAPI(
            delay=delay, timeout=timeout, use_selenium=use_selenium,
        )
        self.downloader = FileDownloader(
            output_dir=output_dir, resume=resume, delay=delay, timeout=timeout,
        )

        self.all_items: list[PearlItem] = []
        self._visited_trees: set[str] = set()
        self._stats = {
            "collections_scanned": 0,
            "files_found": 0,
            "files_downloaded": 0,
            "files_skipped": 0,
            "files_failed": 0,
        }

    def run(self) -> None:
        """Point d'entrée principal du scraping."""
        logger.info("=" * 60)
        logger.info("Pearltrees File Downloader v%s", VERSION)
        logger.info("=" * 60)
        logger.info("URL cible    : %s", self.original_url)
        logger.info("Dossier      : %s", self.output_dir.resolve())
        logger.info("Profondeur   : %d", self.max_depth)
        logger.info("Reprise      : %s", "oui" if self.resume else "non")
        logger.info("=" * 60)

        # Vérifier robots.txt
        self.robots.load()
        if not self.robots.can_fetch(self.original_url):
            logger.error("❌ L'URL est interdite par robots.txt. Abandon.")
            return

        # Extraire le tree ID
        tree_id = PearltreesAPI.extract_tree_id(self.original_url)
        if not tree_id:
            logger.error(
                "❌ Impossible d'extraire le treeId depuis l'URL : %s",
                self.original_url,
            )
            logger.info("Tentative de fallback HTML…")
            self._scrape_html_fallback()
        else:
            logger.info("🔑 Tree ID extrait : %s", tree_id)
            self._scrape_collection(tree_id, depth=0, parent_name="racine")

        # Télécharger les fichiers
        self._download_all_files()

        # Exporter les métadonnées
        if self.metadata_format:
            self._export_metadata()

        # Générer le README
        self._generate_readme()

        # Résumé
        self._print_summary()

        # Nettoyage
        self.api.close()

    def _scrape_collection(
        self, tree_id: str, depth: int, parent_name: str = ""
    ) -> None:
        """Scrape récursivement une collection et ses sous-collections."""
        if tree_id in self._visited_trees:
            logger.debug("Collection déjà visitée : %s", tree_id)
            return
        self._visited_trees.add(tree_id)

        data = self.api.get_collection(tree_id)
        if data is None:
            logger.warning("⚠  Pas de données pour la collection %s", tree_id)
            return

        self._stats["collections_scanned"] += 1

        # Extraire le nom de la collection
        collection_name = parent_name
        if isinstance(data, dict):
            tree_info = data.get("tree", data)
            if isinstance(tree_info, dict):
                collection_name = tree_info.get("title",
                                                 tree_info.get("name", parent_name))

        logger.info("📂 Collection [profondeur %d] : %s (id=%s)",
                     depth, collection_name, tree_id)

        # Parser les perles
        items = self.api._parse_pearls_from_json(data)
        logger.info("   → %d élément(s) trouvé(s)", len(items))

        for item in items:
            item.parent_collection = collection_name

            if item.pearl_type == "collection" and item.ref_tree_id:
                # Sous-collection : récurser si profondeur autorisée
                if depth < self.max_depth:
                    logger.info("   📁 Sous-collection détectée : %s (id=%s)",
                                 item.title, item.ref_tree_id)
                    self._scrape_collection(
                        item.ref_tree_id, depth + 1, item.title or f"sub_{item.ref_tree_id}"
                    )
                else:
                    logger.info(
                        "   ⏭  Sous-collection ignorée (profondeur max atteinte) : %s",
                        item.title,
                    )
            elif item.url:
                # Enrichir avec les détails (best-effort, single attempt)
                if item.pearl_id:
                    try:
                        self.api._rate_limit()
                        resp = self.api.session.get(
                            f"{PEARLTREES_BASE}{API_DETAIL_ENDPOINT}",
                            params={"pearlId": item.pearl_id},
                            timeout=10,
                        )
                        if resp.ok:
                            detail = resp.json()
                            pearl_detail = detail.get("pearl", detail) if isinstance(detail, dict) else {}
                            if isinstance(pearl_detail, dict):
                                if not item.description:
                                    item.description = pearl_detail.get(
                                        "description", pearl_detail.get("excerpt", "")
                                    )
                                tags = pearl_detail.get("tags",
                                                         pearl_detail.get("keywords", []))
                                if isinstance(tags, list) and tags:
                                    item.tags = [str(t) for t in tags]
                    except Exception:
                        logger.debug("Détails indisponibles pour pearl %s (ignoré)", item.pearl_id)

                self.all_items.append(item)
                self._stats["files_found"] += 1

    def _scrape_html_fallback(self) -> None:
        """Fallback HTML si l'extraction d'API échoue."""
        logger.info("🔄 Tentative de scraping HTML pour %s", self.original_url)
        items = self.api.get_page_pearls_html(self.original_url)
        for item in items:
            item.parent_collection = "racine"
            self.all_items.append(item)
            self._stats["files_found"] += 1
        logger.info("   → %d élément(s) trouvé(s) via HTML", len(items))

    def _download_all_files(self) -> None:
        """Télécharge tous les fichiers collectés."""
        downloadable = [
            item for item in self.all_items
            if item.url and _is_downloadable_url(item.url)
        ]

        if not downloadable:
            logger.info("ℹ  Aucun fichier téléchargeable trouvé.")
            # Tenter de télécharger quand même les URLs qui semblent pointer
            # vers des contenus (même sans extension évidente)
            downloadable = [item for item in self.all_items if item.url]
            if not downloadable:
                return
            logger.info(
                "   Tentative de téléchargement de %d URL(s) sans extension connue…",
                len(downloadable)
            )

        logger.info("=" * 60)
        logger.info("📥 Téléchargement de %d fichier(s)…", len(downloadable))
        logger.info("=" * 60)

        with tqdm(total=len(downloadable), desc="Progression globale",
                  unit="fichier", ncols=80) as pbar:
            for item in downloadable:
                filename = _filename_from_url(item.url)
                if item.title and not filename.startswith("http"):
                    # Utiliser le titre comme nom si plus parlant
                    ext = Path(filename).suffix
                    clean_title = _sanitize_filename(item.title)
                    if ext:
                        filename = f"{clean_title}{ext}"
                    elif item.content_type:
                        ext = mimetypes.guess_extension(item.content_type) or ""
                        filename = f"{clean_title}{ext}"

                result = self.downloader.download_file(item.url, filename=filename)
                if result:
                    item.local_path = str(result)
                    item.download_status = "downloaded"
                    self._stats["files_downloaded"] += 1
                else:
                    item.download_status = "error"
                    self._stats["files_failed"] += 1

                pbar.update(1)

    def _export_metadata(self) -> None:
        """Exporte les métadonnées selon le format choisi."""
        if self.metadata_format == "json":
            path = self.output_dir / "metadata.json"
            MetadataExporter.export_json(self.all_items, path)
        elif self.metadata_format == "csv":
            path = self.output_dir / "metadata.csv"
            MetadataExporter.export_csv(self.all_items, path)
        elif self.metadata_format == "both":
            MetadataExporter.export_json(
                self.all_items, self.output_dir / "metadata.json"
            )
            MetadataExporter.export_csv(
                self.all_items, self.output_dir / "metadata.csv"
            )

    def _generate_readme(self) -> None:
        """Génère un fichier README.md avec la liste des fichiers téléchargés."""
        readme_path = self.output_dir / "README.md"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        lines: list[str] = [
            "# Pearltrees Download Report",
            "",
            f"**Date** : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"**Source** : [{self.original_url}]({self.original_url})",
            f"**Fichiers téléchargés** : {self._stats['files_downloaded']}",
            f"**Fichiers échoués** : {self._stats['files_failed']}",
            f"**Collections scannées** : {self._stats['collections_scanned']}",
            "",
            "---",
            "",
            "## Fichiers téléchargés",
            "",
            "| # | Titre | URL | Fichier local | Statut |",
            "|---|-------|-----|---------------|--------|",
        ]

        for i, item in enumerate(self.all_items, 1):
            title = (item.title or "Sans titre").replace("|", "\\|")
            url = item.url or "N/A"
            local = item.local_path or "—"
            status_icon = {
                "downloaded": "✅",
                "skipped": "⏭",
                "error": "❌",
                "pending": "⏳",
            }.get(item.download_status, "❓")
            lines.append(
                f"| {i} | {title[:60]} | [lien]({url}) | `{local}` | {status_icon} |"
            )

        lines.extend([
            "",
            "---",
            "",
            f"*Généré par Pearltrees File Downloader v{VERSION}*",
        ])

        with open(readme_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logger.info("📝 README.md généré : %s", readme_path)

    def _print_summary(self) -> None:
        """Affiche le résumé final."""
        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 RÉSUMÉ")
        logger.info("=" * 60)
        logger.info("  Collections scannées : %d", self._stats["collections_scanned"])
        logger.info("  Fichiers trouvés     : %d", self._stats["files_found"])
        logger.info("  Fichiers téléchargés : %d", self._stats["files_downloaded"])
        logger.info("  Fichiers échoués     : %d", self._stats["files_failed"])
        logger.info("  Dossier de sortie    : %s", self.output_dir.resolve())
        logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _filename_from_url(url: str) -> str:
    """Extrait un nom de fichier depuis une URL."""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    filename = os.path.basename(path)
    if not filename or filename == "/":
        # Générer un nom basé sur le hash de l'URL
        url_hash = hashlib.md5(url.encode()).hexdigest()[:10]
        filename = f"file_{url_hash}"
    return filename


def _sanitize_filename(name: str) -> str:
    """Nettoie un nom de fichier (supprime les caractères interdits)."""
    # Supprimer les caractères non autorisés dans les noms de fichiers Windows
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    # Supprimer les espaces en trop
    name = re.sub(r"\s+", " ", name).strip()
    # Limiter la longueur
    if len(name) > 200:
        stem, ext = os.path.splitext(name)
        name = stem[:200 - len(ext)] + ext
    return name or "unnamed"


def _categorize_file(filename: str) -> str:
    """Détermine la catégorie d'un fichier selon son extension."""
    ext = Path(filename).suffix.lower()
    for category, extensions in FILE_CATEGORIES.items():
        if ext in extensions:
            return category
    return "other"


def _is_downloadable_url(url: str) -> bool:
    """Vérifie si une URL pointe probablement vers un fichier téléchargeable."""
    if not url:
        return False

    parsed = urlparse(url)
    path = parsed.path.lower()

    # Vérifier les extensions connues
    all_extensions = []
    for exts in FILE_CATEGORIES.values():
        all_extensions.extend(exts)

    for ext in all_extensions:
        if path.endswith(ext):
            return True

    # Vérifier les domaines CDN courants de Pearltrees
    domain = parsed.netloc.lower()
    cdn_domains = [
        "cdn-thumbshot-ie.pearltrees.com",
        "s3.amazonaws.com",
        "cdn.pearltrees.com",
    ]
    if any(cdn in domain for cdn in cdn_domains):
        return True

    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def create_parser() -> argparse.ArgumentParser:
    """Crée le parser d'arguments CLI."""
    parser = argparse.ArgumentParser(
        prog="pearltrees_downloader",
        description=(
            "📥 Pearltrees File Downloader v" + VERSION + "\n"
            "Télécharge tous les fichiers d'une collection Pearltrees."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  %(prog)s https://www.pearltrees.com/user/collection/id12345\n"
            "  %(prog)s URL --depth 2 --output mon_dossier\n"
            "  %(prog)s URL --resume --metadata json\n"
            "  %(prog)s URL --selenium --delay 2.0\n"
        ),
    )

    parser.add_argument(
        "url",
        help="URL de la collection Pearltrees à télécharger",
    )
    parser.add_argument(
        "-o", "--output",
        default="downloads",
        help="Dossier de sortie (défaut : downloads)",
    )
    parser.add_argument(
        "-d", "--depth",
        type=int,
        default=0,
        help="Profondeur de scraping des sous-collections (0 = page actuelle uniquement, défaut : 0)",
    )
    parser.add_argument(
        "-r", "--resume",
        action="store_true",
        help="Reprendre un téléchargement interrompu (ignore les fichiers existants)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Délai entre les requêtes en secondes (défaut : {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout des requêtes en secondes (défaut : {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--selenium",
        action="store_true",
        help="Utiliser Selenium comme fallback pour le contenu JavaScript",
    )
    parser.add_argument(
        "-m", "--metadata",
        choices=["json", "csv", "both"],
        default=None,
        help="Format d'export des métadonnées (json, csv, ou both)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Activer le mode verbeux (debug)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )

    return parser


def main() -> None:
    """Point d'entrée du script."""
    parser = create_parser()
    args = parser.parse_args()

    # Valider l'URL
    if "pearltrees.com" not in args.url.lower():
        logger.warning(
            "⚠  L'URL ne semble pas être une page Pearltrees : %s", args.url
        )

    # Configurer le logging
    log_file = os.path.join(args.output, "pearltrees_download.log")
    os.makedirs(args.output, exist_ok=True)
    setup_logging(log_file=log_file, verbose=args.verbose)

    # Lancer le scraper
    scraper = PearltreesScraper(
        url=args.url,
        output_dir=args.output,
        depth=args.depth,
        resume=args.resume,
        delay=args.delay,
        timeout=args.timeout,
        use_selenium=args.selenium,
        metadata_format=args.metadata,
    )

    try:
        scraper.run()
    except KeyboardInterrupt:
        logger.info("\n⚠  Interruption par l'utilisateur (Ctrl+C)")
        logger.info("Vous pouvez reprendre le téléchargement avec l'option --resume")
        scraper._export_metadata()
        scraper._generate_readme()
        scraper._print_summary()
        scraper.api.close()
        sys.exit(1)
    except Exception as e:
        logger.exception("❌ Erreur fatale : %s", e)
        scraper.api.close()
        sys.exit(2)


if __name__ == "__main__":
    main()
