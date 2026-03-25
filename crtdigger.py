#!/usr/bin/env python3
import re
import sys
import os
import time
import json
import csv
import argparse
import socket
import requests
import urllib3
import concurrent.futures
import tldextract
from datetime import datetime
from bs4 import BeautifulSoup
from colorama import init, Fore, Style

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
init(autoreset=True)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0"

# القائمة الأساسية — هتتنسخ وتتعدل حسب الهدف
BASE_FALSE_ACQUISITIONS = {
    "apple inc.", "apple", "facebook", "meta platforms", "google",
    "amazon", "netflix", "comcast", "at&t", "verizon",
    "verizon communications", "best buy", "barnes & noble",
    "electronic arts", "ubisoft", "take-two interactive",
    "nbc universal", "liberty media", "cnbc", "rogers communications",
    "ericsson", "uber", "grab", "databricks", "oyo rooms",
    "london stock exchange group", "steve ballmer", "microsoft",
    "publicis groupe", "the washington post company",
    "line corporation", "phase one", "telewest", "usa networks",
    "ticketmaster", "avid technology", "corel corporation",
    "individual", "perri croshaw", "wemade entertainment",
    "technology crossover ventures", "tci technology ventures",
    "the reynolds and reynolds company", "northgate information solutions",
    "macdonald dettwiler", "envoy communications",
    "jupiter telecommunications", "agency.com", "proginet",
    "orion health asia pacific of new zealand", "leguide.com sa",
    "nokia",
}

ACQ_FALSE_DOMAINS = {
    "archive.org", "wikipedia.org", "wikimedia.org", "wikidata.org", "wiktionary.org",
    "google.com", "youtube.com", "facebook.com", "twitter.com", "linkedin.com",
    "instagram.com", "amazon.com", "apple.com", "netflix.com", "bloomberg.com",
    "example.com", "web.archive.org", "t.co", "bit.ly", "forbes.com", "reuters.com"
}

# ════ بصمات ثغرة Subdomain Takeover ════
TAKEOVER_SIGNATURES = {
    "GitHub": "There isn't a GitHub Pages site here.",
    "Heroku": "No such app",
    "AWS S3": "The specified bucket does not exist",
    "Tumblr": "Whatever you were looking for doesn't currently exist at this address.",
    "WordPress": "Do you want to register",
    "Shopify": "Sorry, this shop is currently unavailable.",
    "Pantheon": "The edgesuite cname record provided was not recognized",
    "Zendesk": "Help Center Closed",
    "DigitalOcean": "Domain uses DO name servers with no droplet configured",
    "Bitbucket": "Repository not found",
    "Fastly": "Fastly error: unknown domain",
    "Ghost": "The thing you were looking for is no longer here",
    "Surge": "project not found",
    "Help Scout": "No settings were found for this company",
}


def banner():
    print(Fore.CYAN + r"""
           _         _ _
 ___ _ __| |_    __| (_) __ _  __ _  ___ _ __
/ __| '__| __|  / _` | |/ _` |/ _` |/ _ \ '__|
| (__| |  | |_  | (_| | | (_| | (_| |  __/ |
 \___|_|   \__|  \__,_|_|\__, |\__, |\___|_|
                         |___/ |___/  """ + Fore.YELLOW + "v3.6" + Fore.CYAN + """
      Ultimate Recon & Acquisition Discovery
                   By: rv_u
    """)


class CrtDigger:

    def __init__(self, args):
        self.args = args
        self.session = requests.Session()
        self.session.headers = {"User-Agent": UA}
        self.session.verify = False
        self.all_domains = set()
        self.all_subdomains = {}
        self.acquisitions = []
        self.acq_names = []
        self.live_results = []
        self.sources_stats = {}
        self.start_time = time.time()
        self.base_dir = ""
        self.state_file = ""
        self._cache = {}
        self.target_domains = set()

        # 🔧 نسخة خاصة — هتتعدل حسب الهدف
        self.false_acq = BASE_FALSE_ACQUISITIONS.copy()
        self.false_domains = ACQ_FALSE_DOMAINS.copy()

    def log(self, lvl, msg):
        colors = {"i": Fore.YELLOW, "s": Fore.GREEN, "e": Fore.RED,
                  "d": Fore.WHITE, "t": Fore.MAGENTA, "c": Fore.CYAN,
                  "w": Fore.RED + Style.BRIGHT}
        print(colors.get(lvl, Fore.WHITE) + msg)

    @staticmethod
    def _clean(text):
        return re.sub(r"^\*\.", "", text.strip().lower())

    def _valid(self, text):
        text = self._clean(text)
        return text if re.match(r"^([a-z0-9-]+\.)+[a-z]{2,}$", text) else None

    @staticmethod
    def _is_domain(text):
        return bool(re.match(r"^([a-z0-9-]+\.)+[a-z]{2,}$", text.lower().strip()))

    def _root(self, domain):
        ext = tldextract.extract(domain)
        return f"{ext.domain}.{ext.suffix}" if ext.domain and ext.suffix else None

    def _add_source(self, name, count):
        self.sources_stats[name] = self.sources_stats.get(name, 0) + count

    def _mkdir(self, path):
        os.makedirs(path, exist_ok=True)
        return path

    def _group_by_root(self, domains):
        groups = {}
        for d in domains:
            r = self._root(d)
            if r:
                groups.setdefault(r, set()).add(d)
        return groups

    def _elapsed(self):
        e = time.time() - self.start_time
        m, s = int(e // 60), int(e % 60)
        return f"{m}m{s}s" if m else f"{s}s"

    def _progress(self, current, total, prefix=""):
        pct = int(current / total * 100) if total else 0
        bar_len = 25
        filled = int(bar_len * current / total) if total else 0
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  {Fore.CYAN}{prefix} [{bar}] {pct}% ({current}/{total}) "
              f"{Fore.YELLOW}[{self._elapsed()}]   ",
              end="", flush=True)
        if current >= total:
            print()

    def _get_org_variations(self, org):
        org = org.strip()
        variations = [org]
        suffixes = ["Corporation", "Corp", "Corp.", "Inc", "Inc.",
                    "LLC", "Ltd", "Ltd.", "Limited",
                    "Technologies", "Software", "Systems"]
        has_suffix = False
        for s in suffixes:
            if org.lower().endswith(s.lower()):
                has_suffix = True
                base = org[:-(len(s))].strip().rstrip(",")
                if base:
                    variations.append(base)
                break
        if not has_suffix:
            for s in suffixes[:6]:
                variations.append(f"{org} {s}")
        seen = set()
        unique = []
        for v in variations:
            if v.lower() not in seen:
                seen.add(v.lower())
                unique.append(v)
        return unique

    def _adjust_filters_for_target(self, targets):
        removed = []
        for t in targets:
            t_lower = t.lower().strip()
            suffixes = ["", " inc.", " inc", " llc", " ltd", " ltd.",
                        " corporation", " corp", " corp.",
                        " technologies", " software", " systems",
                        " communications", " networks"]

            for suffix in suffixes:
                name = t_lower + suffix
                if name in self.false_acq:
                    self.false_acq.discard(name)
                    removed.append(name)

            if self._is_domain(t_lower):
                root = self._root(t_lower)
                if root and root in self.false_domains:
                    self.false_domains.discard(root)
                    removed.append(root)

            clean = re.sub(r'[^a-z0-9]', '', t_lower)
            for tld in [".com", ".net", ".org", ".io"]:
                possible = clean + tld
                if possible in self.false_domains:
                    self.false_domains.discard(possible)
                    removed.append(possible)

        if removed:
            self.log("i", f"  [*] Adjusted filters for target: "
                          f"removed {removed}")

    def _crtsh_request(self, url):
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self.session.get(url, timeout=60)
                if r.status_code == 200:
                    return r
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = min(30, 5 * attempt)
                    print(Fore.RED +
                        f"\n    [!] crt.sh {r.status_code} "
                        f"— retry in {wait}s (attempt {attempt})...",
                        end="", flush=True)
                    time.sleep(wait)
                    continue
                else:
                    return None
            except requests.exceptions.Timeout:
                wait = min(30, 5 * attempt)
                print(Fore.RED +
                    f"\n    [!] timeout — retry in {wait}s...",
                    end="", flush=True)
                time.sleep(wait)
            except requests.exceptions.ConnectionError:
                wait = min(60, 10 * attempt)
                print(Fore.RED +
                    f"\n    [!] connection error — retry in {wait}s...",
                    end="", flush=True)
                time.sleep(wait)
            except Exception:
                time.sleep(min(30, 5 * attempt))

    def collect_crtsh_org(self, org):
        if org in self._cache:
            return self._cache[org]
        domains = set()
        encoded = requests.utils.quote(org)
        url = f"https://crt.sh/?O=%25{encoded}%25"
        r = self._crtsh_request(url)
        if r:
            soup = BeautifulSoup(r.text, "html.parser")
            for td in soup.find_all("td"):
                for t in td.stripped_strings:
                    v = self._valid(t)
                    if v:
                        domains.add(v)
        self._cache[org] = domains
        return domains

    def collect_crtsh_json(self, domain):
        if f"json:{domain}" in self._cache:
            return self._cache[f"json:{domain}"]
        domains = set()
        url = f"https://crt.sh/?q=%25.{domain}&output=json"
        r = self._crtsh_request(url)
        if r:
            try:
                data = r.json()
                for entry in data:
                    for field in ("common_name", "name_value"):
                        value = entry.get(field)
                        if value is None:
                            continue
                        if not isinstance(value, str):
                            value = str(value)
                        for line in value.split("\n"):
                            v = self._valid(line)
                            if v:
                                domains.add(v)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        self._cache[f"json:{domain}"] = domains
        return domains

    def collect_crtsh_org_smart(self, org):
        variations = self._get_org_variations(org)
        all_found = set()
        for i, var in enumerate(variations):
            print(Fore.WHITE + f"    [crt.sh] \"{var}\"  ", end="", flush=True)
            found = self.collect_crtsh_org(var)
            print(Fore.GREEN + f"→  {len(found)}")
            all_found.update(found)
            if found and i == 0:
                break
            if len(all_found) > 0 and i >= 2:
                break
            time.sleep(3)
        return all_found

    def collect_certspotter(self, domain):
        domains = set()
        try:
            url = f"https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names"
            r = self.session.get(url, timeout=20)
            if r.status_code == 200:
                for entry in r.json():
                    for name in entry.get("dns_names", []):
                        v = self._valid(name)
                        if v:
                            domains.add(v)
        except Exception:
            pass
        return domains

    def collect_rapiddns(self, domain):
        domains = set()
        try:
            r = self.session.get(
                f"https://rapiddns.io/subdomain/{domain}?full=1", timeout=20)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                table = soup.find("table")
                if table:
                    for row in table.find_all("tr"):
                        cells = row.find_all("td")
                        if cells:
                            v = self._valid(cells[0].get_text())
                            if v:
                                domains.add(v)
        except Exception:
            pass
        return domains

    def collect_hackertarget(self, domain):
        domains = set()
        try:
            r = self.session.get(
                f"https://api.hackertarget.com/hostsearch/?q={domain}", timeout=15)
            if r.status_code == 200 and "error" not in r.text.lower():
                for line in r.text.strip().split("\n"):
                    v = self._valid(line.split(",")[0])
                    if v:
                        domains.add(v)
        except Exception:
            pass
        return domains

    def collect_alienvault(self, domain):
        domains = set()
        try:
            url = f"https://otx.alienvault.com/api/v1/indicators/domain/{domain}/passive_dns"
            r = self.session.get(url, timeout=15)
            if r.status_code == 200:
                for entry in r.json().get("passive_dns", []):
                    v = self._valid(entry.get("hostname", ""))
                    if v:
                        domains.add(v)
        except Exception:
            pass
        return domains

    def collect_urlscan(self, domain):
        domains = set()
        try:
            url = f"https://urlscan.io/api/v1/search/?q=domain:{domain}&size=500"
            r = self.session.get(url, timeout=15)
            if r.status_code == 200:
                for res in r.json().get("results", []):
                    v = self._valid(res.get("page", {}).get("domain", ""))
                    if v:
                        domains.add(v)
        except Exception:
            pass
        return domains

    def collect_wayback(self, domain):
        domains = set()
        try:
            url = (f"https://web.archive.org/cdx/search/cdx"
                   f"?url=*.{domain}/*&output=json&fl=original"
                   f"&collapse=urlkey&limit=5000")
            r = self.session.get(url, timeout=25)
            if r.status_code == 200:
                data = r.json()
                for row in data[1:]:
                    try:
                        parsed = re.findall(r"https?://([^/:]+)", row[0])
                        if parsed:
                            v = self._valid(parsed[0])
                            if v:
                                domains.add(v)
                    except Exception:
                        pass
        except Exception:
            pass
        return domains

    def collect_anubis(self, domain):
        domains = set()
        try:
            url = f"https://jldc.me/anubis/subdomains/{domain}"
            r = self.session.get(url, timeout=15)
            if r.status_code == 200:
                for sub in r.json():
                    v = self._valid(sub)
                    if v:
                        domains.add(v)
        except Exception:
            pass
        return domains

    def collect_shrewdeye(self, domain):
        domains = set()
        try:
            url = f"https://shrewdeye.app/domains/{domain}.txt"
            r = self.session.get(url, timeout=15)
            if r.status_code == 200:
                for line in r.text.strip().split("\n"):
                    v = self._valid(line.strip())
                    if v:
                        domains.add(v)
        except Exception:
            pass
        return domains

    def collect_domain_all_sources(self, domain):
        all_found = set()
        sources = [
            ("crt.sh", self.collect_crtsh_json),
            ("CertSpotter", self.collect_certspotter),
            ("RapidDNS", self.collect_rapiddns),
            ("HackerTarget", self.collect_hackertarget),
            ("AlienVault", self.collect_alienvault),
        ]
        if self.args.deep:
            sources.extend([
                ("urlscan.io", self.collect_urlscan),
                ("Wayback", self.collect_wayback),
                ("Shrewdeye", self.collect_shrewdeye),
                ("Anubis", self.collect_anubis),
            ])
        for name, func in sources:
            print(Fore.WHITE + f"    [{name}]  ", end="", flush=True)
            try:
                res = func(domain)
                print(Fore.GREEN + f"→  {len(res)}")
                all_found.update(res)
                self._add_source(name, len(res))
            except Exception:
                print(Fore.RED + "→  error")
            time.sleep(0.3)
        return all_found

    def _extract_domain_from_url(self, url):
        try:
            d = re.sub(r"^https?://(www\.)?", "", url).strip("/")
            d = d.split("/")[0].split("?")[0].split("#")[0]
            root = self._root(d)
            return root
        except Exception:
            return None

    def _get_domain_from_wiki(self, company_name, is_acquisition=False):
        try:
            wiki = company_name.replace(" ", "_")
            r = self.session.get(
                f"https://en.wikipedia.org/wiki/{wiki}", timeout=10)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")

            title = soup.find("h1", id="firstHeading")
            if title:
                title_text = title.get_text(strip=True).lower()
                company_lower = company_name.lower()
                company_words = set(company_lower.split())
                title_words = set(title_text.split())
                if not company_words & title_words:
                    return None

            infobox = soup.find("table", class_="infobox")
            if infobox:
                for row in infobox.find_all("tr"):
                    th = row.find("th")
                    if not th:
                        continue
                    th_text = th.get_text().lower()
                    if "website" not in th_text and "url" not in th_text:
                        continue

                    td = row.find("td")
                    if not td:
                        continue

                    for link in td.find_all("a", href=True):
                        href = link["href"]
                        if "http" in href:
                            domain = self._extract_domain_from_url(href)
                            if domain:
                                if is_acquisition:
                                    if domain in self.false_domains:
                                        continue
                                    if domain in self.target_domains:
                                        continue
                                return domain

                    text = td.get_text(strip=True)
                    text = re.sub(r'^www\.', '', text)
                    if self._is_domain(text):
                        root = self._root(text)
                        if root:
                            if is_acquisition:
                                if root in self.false_domains:
                                    continue
                                if root in self.target_domains:
                                    continue
                            return root

            ext_section = soup.find("span", id="External_links")
            if ext_section:
                parent = ext_section.find_parent()
                if parent:
                    sib = parent.find_next_sibling()
                    if sib:
                        for link in sib.find_all("a", href=True)[:5]:
                            href = link["href"]
                            if "http" in href and "wiki" not in href:
                                domain = self._extract_domain_from_url(href)
                                if domain:
                                    if is_acquisition and domain in self.false_domains:
                                        continue
                                    return domain

            for link in soup.find_all("a", href=True):
                text = link.get_text(strip=True).lower()
                if "official website" in text or "official site" in text:
                    domain = self._extract_domain_from_url(link["href"])
                    if domain:
                        if is_acquisition and domain in self.false_domains:
                            continue
                        return domain

        except Exception:
            pass
        return None

    def find_main_domain(self, org):
        wiki_domain = self._get_domain_from_wiki(org, is_acquisition=False)
        if wiki_domain:
            return wiki_domain
        return self._guess_domain_main(org)

    def _guess_domain_main(self, company_name):
        clean = company_name.lower()
        for suffix in [" inc.", " inc", " llc", " ltd",
                       " corporation", " corp", " corp."]:
            clean = clean.replace(suffix, "")
        clean = clean.strip()
        clean = re.sub(r'[^a-z0-9]', '', clean)
        if not clean or len(clean) < 2:
            return None
        for tld in [".com", ".net", ".io", ".org"]:
            guess = clean + tld
            try:
                socket.gethostbyname(guess)
                return guess
            except socket.error:
                continue
        return None

    def _guess_domain_smart(self, company_name):
        clean = company_name.lower()
        for suffix in [" inc.", " inc", " llc", " ltd", " ltd.",
                       " corporation", " corp", " corp.",
                       " software", " technologies", " systems",
                       " studios", " games", " networks",
                       " communications", " solutions",
                       ", inc.", ", inc", ", llc"]:
            clean = clean.replace(suffix, "")
        clean = clean.strip()
        clean = re.sub(r'[^a-z0-9]', '', clean)

        if not clean or len(clean) < 3:
            return None

        for tld in [".com", ".net", ".io", ".org"]:
            guess = clean + tld
            try:
                socket.gethostbyname(guess)
            except socket.error:
                continue

            if guess in self.false_domains or guess in self.target_domains:
                continue

            if self._verify_domain_belongs(guess, company_name):
                return guess

        return None

    def _verify_domain_belongs(self, domain, company_name):
        try:
            r = requests.get(
                f"https://{domain}",
                timeout=5, verify=False,
                allow_redirects=True,
                headers={"User-Agent": UA})

            if r.status_code != 200:
                r = requests.get(
                    f"http://{domain}",
                    timeout=5, verify=False,
                    allow_redirects=True,
                    headers={"User-Agent": UA})

            if r.status_code != 200:
                return False

            content = r.text[:5000].lower()
            title = ""
            try:
                soup = BeautifulSoup(content, "html.parser")
                t = soup.find("title")
                if t:
                    title = t.get_text(strip=True).lower()
            except Exception:
                pass

            company_words = set(
                re.sub(r'[^a-z0-9\s]', '', company_name.lower()).split()
            )
            company_words -= {"inc", "llc", "ltd", "corp",
                              "corporation", "software",
                              "the", "and", "of", "for"}

            if not company_words:
                return False

            text_to_check = title + " " + content

            matches = sum(1 for w in company_words
                         if w in text_to_check and len(w) > 2)

            if matches >= 1:
                return True

            final_url = r.url.lower()
            for parent in self.target_domains:
                if parent in final_url:
                    return False

            parked_signs = [
                "domain for sale", "buy this domain",
                "parked", "this domain", "coming soon",
                "under construction", "godaddy",
                "namecheap", "dan.com", "sedo.com",
                "hugedomains", "afternic"
            ]
            for sign in parked_signs:
                if sign in content:
                    return False

            return False

        except Exception:
            return False

    def discover_acquisitions_wiki(self, company, is_sub=False):
        if not is_sub:
            self.log("t", f"\n{'═' * 55}")
            self.log("t", f" 🔍  ACQUISITION DISCOVERY : {company}")
            self.log("t", f"{'═' * 55}")
        else:
            print(Fore.CYAN + f"  [>] Checking sub-acquisitions for: {company}...")

        acqs = self._wiki_acq_list(company)
        if not acqs:
            acqs = self._wiki_acq_section(company)

        seen, clean = set(), []
        for a in acqs:
            a = re.sub(r"\[.*?\]", "", a).strip()
            a = re.sub(r"\(.*?\)$", "", a).strip()
            a = re.sub(r"^[\d\.\s]+", "", a).strip()
            if not a or len(a) < 3 or a.isdigit():
                continue
            if a.lower() in seen:
                continue
            if a.lower() in self.false_acq:
                continue
            if re.match(r"^[\d\$\€\£]", a):
                continue
            seen.add(a.lower())
            clean.append(a)

        filtered_out = len(acqs) - len(clean)

        if clean:
            if not is_sub:
                self.log("s", f"\n  [+] Found {len(clean)} acquisitions"
                         f" (filtered {filtered_out} false positives)\n")
                for i, a in enumerate(clean, 1):
                    print(Fore.WHITE + f"      {i:3}. {a}")
            else:
                self.log("s", f"      [+] Found {len(clean)} sub-acquisitions")
        else:
            if not is_sub:
                self.log("e", "  [-] No acquisitions found")

        return clean

    def _wiki_acq_list(self, company):
        wiki = company.replace(" ", "_")
        for pat in [
            f"List_of_mergers_and_acquisitions_by_{wiki}",
            f"List_of_acquisitions_by_{wiki}",
        ]:
            try:
                r = self.session.get(
                    f"https://en.wikipedia.org/wiki/{pat}", timeout=15)
                if r.status_code == 200:
                    return self._parse_wiki_tables(r.text)
            except Exception:
                continue
        return []

    def _wiki_acq_section(self, company):
        wiki = company.replace(" ", "_")
        acqs = []
        try:
            r = self.session.get(
                f"https://en.wikipedia.org/wiki/{wiki}", timeout=15)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            for heading in soup.find_all(["h2", "h3"]):
                span = heading.find("span", class_="mw-headline")
                if not span or "acqui" not in span.get_text().lower():
                    continue
                sib = heading.find_next_sibling()
                while sib and sib.name not in ("h2",):
                    if sib.name == "table":
                        acqs.extend(self._parse_wiki_tables(str(sib)))
                    elif sib.name in ("ul", "ol"):
                        for li in sib.find_all("li"):
                            link = li.find("a")
                            if link:
                                acqs.append(link.get_text(strip=True))
                    sib = sib.find_next_sibling()
        except Exception:
            pass
        return acqs

    def _parse_wiki_tables(self, html):
        names = []
        soup = BeautifulSoup(html, "html.parser") if isinstance(html, str) else html
        for table in soup.find_all("table", class_="wikitable"):
            hdr = table.find("tr")
            if not hdr:
                continue
            headers = [h.get_text(strip=True).lower() for h in hdr.find_all("th")]
            col = 1
            for i, h in enumerate(headers):
                if any(k in h for k in ("company", "acqui", "target", "name", "business")):
                    col = i
                    break
            for row in table.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= col:
                    continue
                cell = cells[col]
                link = cell.find("a")
                name = link.get_text(strip=True) if link else cell.get_text(strip=True)
                if name:
                    names.append(name)
        return names

    def resolve_all_acquisitions(self, acq_names):
        self.log("t", f"\n{'═' * 55}")
        self.log("t", f" 🌐  RESOLVING ACQUISITION DOMAINS")
        self.log("t", f"{'═' * 55}")

        if self.args.guess:
            self.log("i", "  [*] Guessing enabled (--guess)")
        else:
            self.log("i", "  [*] Guessing disabled (use --guess to enable)")

        print()

        resolved = []
        found = 0
        guessed = 0
        not_found = 0

        for i, name in enumerate(acq_names, 1):
            print(Fore.WHITE +
                  f"  [{i}/{len(acq_names)}] {name}  ",
                  end="", flush=True)

            domain = self._get_domain_from_wiki(name, is_acquisition=True)
            source = "wiki"

            if not domain and self.args.guess:
                domain = self._guess_domain_smart(name)
                source = "guess"

            if domain:
                if source == "wiki":
                    found += 1
                    print(Fore.GREEN + f"→  {domain}")
                else:
                    guessed += 1
                    print(Fore.YELLOW + f"→  {domain} ⚠ guessed")
                resolved.append({
                    "name": name, "domain": domain,
                    "source": source, "status": "found"
                })
            else:
                not_found += 1
                print(Fore.RED + "→  no domain")
                resolved.append({
                    "name": name, "domain": None,
                    "source": None, "status": "not_found"
                })

            time.sleep(0.2)

        self.log("s", f"\n  [+] Wikipedia: {found} | "
                      + (f"Guessed: {guessed} | " if self.args.guess else "")
                      + f"Not found: {not_found}")

        return resolved

    def probe_domain(self, domain):
        info = dict(domain=domain, alive=False, ip=None,
                    status=None, title=None, server=None, proto=None)
        try:
            info["ip"] = socket.gethostbyname(domain)
        except Exception:
            pass
        for proto in ("https", "http"):
            try:
                r = requests.get(
                    f"{proto}://{domain}",
                    timeout=self.args.timeout, verify=False,
                    allow_redirects=True, headers={"User-Agent": UA})
                info.update(alive=True, proto=proto,
                            status=r.status_code,
                            server=r.headers.get("Server", ""))
                try:
                    s = BeautifulSoup(r.text[:3000], "html.parser")
                    t = s.find("title")
                    if t:
                        info["title"] = t.get_text(strip=True)[:60]
                except Exception:
                    pass
                return info
            except Exception:
                continue
        return info

    def check_takeover(self, domain):
        info = {"domain": domain, "vulnerable": False, "provider": None}
        for proto in ("http", "https"):
            try:
                r = requests.get(
                    f"{proto}://{domain}",
                    timeout=self.args.timeout, 
                    verify=False,
                    allow_redirects=True, 
                    headers={"User-Agent": UA}
                )
                
                for provider, signature in TAKEOVER_SIGNATURES.items():
                    if signature.lower() in r.text.lower():
                        info["vulnerable"] = True
                        info["provider"] = provider
                        return info
            except Exception:
                continue
        return info

    def _save_state(self, step="", extra=None):
        state = {
            "step": step,
            "all_domains": list(self.all_domains),
            "all_subdomains": {k: list(v) for k, v in self.all_subdomains.items()},
            "acq_names": self.acq_names,
            "acquisitions": self.acquisitions,
            "sources_stats": self.sources_stats,
            "target_domains": list(self.target_domains),
        }
        if extra:
            state.update(extra)
        with open(self.state_file, "w") as f:
            json.dump(state, f, ensure_ascii=False)

    def _load_state(self):
        if os.path.isfile(self.state_file):
            with open(self.state_file) as f:
                state = json.load(f)
            self.all_domains = set(state.get("all_domains", []))
            self.all_subdomains = {
                k: set(v) for k, v in state.get("all_subdomains", {}).items()}
            self.acq_names = state.get("acq_names", [])
            self.acquisitions = state.get("acquisitions", [])
            self.sources_stats = state.get("sources_stats", {})
            self.target_domains = set(state.get("target_domains", []))
            self.log("s", f"[+] Resumed: {len(self.all_domains)} domains")
            return state
        return {}

    def setup_folders(self, target_name):
        safe_name = re.sub(r'[^\w\s-]', '', target_name).strip().replace(' ', '_')
        self.base_dir = self._mkdir(safe_name)
        self._mkdir(os.path.join(self.base_dir, "subdomains"))
        self._mkdir(os.path.join(self.base_dir, "acquisitions"))
        self.state_file = os.path.join(self.base_dir, ".state.json")
        self.log("s", f"\n[+] Output: {self.base_dir}/")

    def _update_sub_files(self):
        sub_dir = os.path.join(self.base_dir, "subdomains")
        for root, subs in self.all_subdomains.items():
            filepath = os.path.join(sub_dir, f"{root}.txt")
            with open(filepath, "w") as f:
                for s in sorted(subs):
                    f.write(s + "\n")

    def run(self):
        targets = []
        if os.path.isfile(self.args.target):
            with open(self.args.target) as f:
                targets = [l.strip() for l in f if l.strip()]
        else:
            targets.append(self.args.target)

        self._adjust_filters_for_target(targets)

        folder_name = targets[0] if len(targets) == 1 else \
            os.path.basename(self.args.target).replace(".txt", "")
        self.setup_folders(folder_name)

        state = {}
        resumed_step = ""
        if self.args.resume:
            state = self._load_state()
            resumed_step = state.get("step", "")

        # ═══ STEP 1 ═══
        if resumed_step not in ("step2a", "step2b", "step3", "done"):
            self.log("t", f"\n{'═' * 55}")
            self.log("t", " 🌐  STEP 1 : DOMAIN DISCOVERY")
            self.log("t", f"{'═' * 55}")

            for org in targets:
                if self._is_domain(org):
                    self.log("i", f"\n[*] {org} (domain mode)")
                    self.target_domains.add(org)
                    found = self.collect_domain_all_sources(org)
                    self.all_domains.update(found)
                else:
                    self.log("i", f"\n[*] {org} (org mode)")

                    self.log("i", "  ┌─ crt.sh org search:")
                    org_found = self.collect_crtsh_org_smart(org)
                    self.all_domains.update(org_found)

                    self.log("i", "\n  ├─ Finding main domain...")
                    main_domain = self.find_main_domain(org)

                    if main_domain:
                        self.log("s", f"  │  Main domain: {main_domain}")
                        self.target_domains.add(main_domain)
                        self.log("i", f"  └─ Scanning {main_domain}:")
                        domain_found = self.collect_domain_all_sources(main_domain)
                        self.all_domains.update(domain_found)
                    else:
                        self.log("e", "  └─ Could not find main domain")

                time.sleep(2)

            for d in self.all_domains:
                r = self._root(d)
                if r:
                    self.target_domains.add(r)

            self.all_subdomains = self._group_by_root(self.all_domains)
            self._update_sub_files()
            self.log("s", f"\n  [+] Root domains: {len(self.all_subdomains)}")
            self.log("s", f"  [+] Total subs: {len(self.all_domains)}")
            self._save_state("step1")

        # ═══ STEP 2A: RESOLVE ═══
        resolved_acqs = []

        if self.args.acquisitions:
            if resumed_step == "step2b":
                resolved_acqs = state.get("resolved_acqs", [])
                self.acq_names = state.get("acq_names", [])
                self.log("s", f"[+] Loaded {len(resolved_acqs)} resolved")

            elif resumed_step not in ("step3", "done"):
                for org in targets:
                    if self._is_domain(org):
                        continue
                    
                    # 1. Level 1
                    l1_acqs = self.discover_acquisitions_wiki(org)
                    all_acqs = list(l1_acqs)
                    
                    # 2. Level 2
                    if l1_acqs and self.args.deep_acq:
                        self.log("t", f"\n{'═' * 55}")
                        self.log("t", f" 🔍  LEVEL 2: ACQUISITIONS OF ACQUISITIONS")
                        self.log("t", f"{'═' * 55}")
                        for acq in l1_acqs:
                            l2_acqs = self.discover_acquisitions_wiki(acq, is_sub=True)
                            for sub_acq in l2_acqs:
                                if sub_acq.lower() not in [x.lower() for x in all_acqs]:
                                    all_acqs.append(sub_acq)

                    # ---> بداية التعديل: فلترة التارجت الأساسي عشان ما يطلعش كاستحواذ <---
                    clean_org = re.sub(r'(?i)\s+(inc\.?|corp\.?|corporation|llc|ltd\.?)$', '', org).strip().lower()
                    
                    final_acqs = []
                    for a in all_acqs:
                        clean_a = re.sub(r'(?i)\s+(inc\.?|corp\.?|corporation|llc|ltd\.?)$', '', a).strip().lower()
                        # لو اسم الاستحواذ مش هو هو اسم التارجت، ضيفه للقائمة
                        if clean_a != clean_org:
                            final_acqs.append(a)
                    
                    self.acq_names.extend(final_acqs)
                    # ---> نهاية التعديل <---
                
                if self.acq_names:
                    # Remove duplicates across all targets
                    self.acq_names = list(dict.fromkeys(self.acq_names))
                    resolved_acqs = self.resolve_all_acquisitions(self.acq_names)
                    self._save_state("step2a", {"resolved_acqs": resolved_acqs})

            # ═══ STEP 2B: SCAN ═══
            if resolved_acqs and resumed_step not in ("step3", "done"):
                with_domain = [a for a in resolved_acqs if a["domain"]]
                without_domain = [a for a in resolved_acqs if not a["domain"]]

                self.log("t", f"\n{'═' * 55}")
                self.log("t", f" 🔎  STEP 2B : SCANNING ACQUISITIONS")
                self.log("t", f"{'═' * 55}")
                self.log("s", f"  [+] With domain: {len(with_domain)}")
                self.log("i", f"  [*] Without domain: {len(without_domain)} (org search)\n")

                acq_dir = os.path.join(self.base_dir, "acquisitions")
                total = len(resolved_acqs)
                found_count = 0
                start_from = 0

                if resumed_step == "step2b":
                    start_from = state.get("acq_scanned", 0)
                    found_count = state.get("acq_found", 0)
                    self.log("i", f"  [*] Resuming from #{start_from + 1}\n")

                for idx, acq_data in enumerate(resolved_acqs):
                    if idx < start_from:
                        continue

                    scanned = idx + 1
                    acq_name = acq_data["name"]
                    wiki_domain = acq_data["domain"]

                    safe_acq = re.sub(r'[^\w\s-]', '', acq_name).strip().replace(' ', '_')
                    if not safe_acq:
                        continue
                    acq_folder = self._mkdir(os.path.join(acq_dir, safe_acq))
                    acq_subs = set()

                    if wiki_domain:
                        found_count += 1
                        source_tag = ""
                        if acq_data.get("source") == "guess":
                            source_tag = Fore.CYAN + " (guessed)"
                        print(Fore.GREEN +
                              f"  [{scanned}/{total}] {acq_name}"
                              f"  →  {wiki_domain}{source_tag}")

                        print(Fore.WHITE + "      [crt.sh]  ",
                              end="", flush=True)
                        found = self.collect_crtsh_json(wiki_domain)
                        print(Fore.GREEN + f"→  {len(found)}")
                        acq_subs.update(found)
                        self._add_source("crt.sh/acq", len(found))

                        if self.args.deep:
                            for name, func in [
                                ("CertSpotter", self.collect_certspotter),
                                ("RapidDNS", self.collect_rapiddns),
                                ("HackerTarget", self.collect_hackertarget),
                                ("Anubis", self.collect_anubis),
                            ]:
                                print(Fore.WHITE + f"      [{name}]  ",
                                      end="", flush=True)
                                try:
                                    res = func(wiki_domain)
                                    print(Fore.GREEN + f"→  {len(res)}")
                                    acq_subs.update(res)
                                    self._add_source(name, len(res))
                                except Exception:
                                    print(Fore.RED + "→  error")
                                time.sleep(0.3)

                    else:
                        print(Fore.YELLOW +
                              f"  [{scanned}/{total}] {acq_name}"
                              f"  →  no domain, org search...",
                              end="", flush=True)
                        found = self.collect_crtsh_org(acq_name)
                        if found:
                            found_count += 1
                            print(Fore.GREEN + f"  ✓ {len(found)}")
                            acq_subs.update(found)
                            self._add_source("crt.sh/acq-org", len(found))
                        else:
                            print(Fore.RED + "  ✗ 0")

                    time.sleep(0.5)

                    acq_info = {
                        "name": acq_name, "domain": wiki_domain,
                        "subdomains_count": len(acq_subs),
                        "roots": list(set(self._root(d) for d in acq_subs if self._root(d)))
                    }

                    with open(os.path.join(acq_folder, "info.txt"), "w") as f:
                        f.write(f"Company: {acq_name}\n")
                        f.write(f"Domain: {wiki_domain or 'Unknown'}\n")
                        f.write(f"Subdomains: {len(acq_subs)}\n")
                        f.write(f"Roots: {', '.join(acq_info['roots'])}\n")

                    if acq_subs:
                        with open(os.path.join(acq_folder, "subdomains.txt"), "w") as f:
                            for s in sorted(acq_subs):
                                f.write(s + "\n")
                        for root, subs in self._group_by_root(acq_subs).items():
                            with open(os.path.join(acq_folder, f"{root}.txt"), "w") as f:
                                for s in sorted(subs):
                                    f.write(s + "\n")

                    self.acquisitions.append(acq_info)
                    self.all_domains.update(acq_subs)
                    for root, subs in self._group_by_root(acq_subs).items():
                        self.all_subdomains.setdefault(root, set()).update(subs)

                    if scanned % 10 == 0:
                        self._save_state("step2b", {
                            "resolved_acqs": resolved_acqs,
                            "acq_scanned": scanned,
                            "acq_found": found_count,
                        })
                        self._update_sub_files()
                        self.log("i",
                            f"\n  ─── [{self._elapsed()}] "
                            f"Progress: {scanned}/{total} "
                            f"| Found: {found_count} "
                            f"| Domains: {len(self.all_domains)} ───\n")

                self.log("s", f"\n  [+] Scanned: {total}/{total}")
                self.log("s", f"  [+] With results: {found_count}")
                self.log("s", f"  [+] Total domains: {len(self.all_domains)}")
                self._update_sub_files()
                self._save_state("step2b")

        # ═══ STEP 3: DEEP SCAN ═══
        if self.args.deep and resumed_step not in ("done",):
            roots = set()
            for d in self.all_domains:
                r = self._root(d)
                if r:
                    roots.add(r)
            already = set()
            for key in self._cache:
                if key.startswith("json:"):
                    already.add(key[5:])
            new_roots = roots - already

            if new_roots:
                self.log("t", f"\n{'═' * 55}")
                self.log("t", f" 🔬  STEP 3 : DEEP SCAN ({len(new_roots)} new roots)")
                self.log("t", f"{'═' * 55}\n")
                total = len(new_roots)
                for i, root in enumerate(sorted(new_roots), 1):
                    self.log("i", f"  [{i}/{total}] {root}")
                    for name, func in [
                        ("crt.sh", self.collect_crtsh_json),
                        ("CertSpotter", self.collect_certspotter),
                        ("RapidDNS", self.collect_rapiddns),
                        ("HackerTarget", self.collect_hackertarget),
                        ("AlienVault", self.collect_alienvault),
                        ("urlscan.io", self.collect_urlscan),
                        ("Wayback", self.collect_wayback),
                        ("Anubis", self.collect_anubis),
                    ]:
                        print(Fore.WHITE + f"    [{name}]  ", end="", flush=True)
                        try:
                            res = func(root)
                            print(Fore.GREEN + f"→  {len(res)}")
                            self.all_domains.update(res)
                            self._add_source(name, len(res))
                            for d in res:
                                r2 = self._root(d)
                                if r2:
                                    self.all_subdomains.setdefault(r2, set()).add(d)
                        except Exception:
                            print(Fore.RED + "→  error")
                        time.sleep(0.3)
                    if i % 5 == 0:
                        self._save_state("step3")
                    time.sleep(0.5)
                self._update_sub_files()
                self._save_state("step3")

        # ═══ RESULTS ═══
        if not self.all_domains:
            self.log("e", "\n[-] No domains found.")
            sys.exit(0)

        all_roots = set()
        for d in self.all_domains:
            r = self._root(d)
            if r:
                all_roots.add(r)

        self.log("s", f"\n[+] Total subdomains: {len(self.all_domains)}")
        self.log("s", f"[+] Total root domains: {len(all_roots)}")

        # ═══ PROBING ═══
        probe_targets = all_roots if not self.args.vertical else self.all_domains

        if self.args.probe:
            self.log("t", f"\n{'═' * 55}")
            self.log("t", " ⚡  LIVE HOST PROBING")
            self.log("t", f"{'═' * 55}")
            mode_name = "subdomains" if self.args.vertical else "root domains"
            self.log("i",
                f"[*] Probing {len(probe_targets)} {mode_name} "
                f"({self.args.threads} threads)\n")

            done = 0
            total = len(probe_targets)
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=self.args.threads) as pool:
                futs = {pool.submit(self.probe_domain, d): d
                        for d in probe_targets}
                for fut in concurrent.futures.as_completed(futs):
                    done += 1
                    r = fut.result()
                    if not r["alive"]:
                        self._progress(done, total, "Probing")
                        continue
                    self.live_results.append(r)
                    sc = r["status"]
                    clr = (Fore.GREEN if sc < 300 else
                           Fore.YELLOW if sc < 400 else
                           Fore.RED if sc < 500 else Fore.MAGENTA)
                    print(f"\r{' ' * 90}\r", end="")
                    print(
                        f"  {clr}[{sc}] {Fore.WHITE}{r['proto']}://{r['domain']}"
                        f"  {Fore.CYAN}{r['ip'] or '?'}  "
                        f"{Fore.YELLOW}{r['server'] or ''}  "
                        f"{Fore.WHITE}{r['title'] or ''}")

            self.log("s", f"\n[+] Live: {len(self.live_results)} / {total}")
        else:
            self.log("t", f"\n{'═' * 55}")
            self.log("t", " 📋  ROOT DOMAINS")
            self.log("t", f"{'═' * 55}\n")
            for d in sorted(all_roots):
                count = len(self.all_subdomains.get(d, set()))
                print(Fore.WHITE + f"  {d}" + Fore.CYAN + f"  ({count} subs)")

        # ═══ TAKEOVER CHECK ═══
        if self.args.takeover:
            self.log("t", f"\n{'═' * 55}")
            self.log("t", " ☠️  SUBDOMAIN TAKEOVER HUNTING")
            self.log("t", f"{'═' * 55}")
            
            takeover_targets = all_roots if not self.args.vertical else self.all_domains
            self.log("i", f"[*] Hunting in {len(takeover_targets)} domains...\n")
            
            takeovers_found = []
            done_tk = 0
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.args.threads) as pool:
                futs = {pool.submit(self.check_takeover, d): d for d in takeover_targets}
                for fut in concurrent.futures.as_completed(futs):
                    done_tk += 1
                    r = fut.result()
                    if r["vulnerable"]:
                        takeovers_found.append(r)
                        print(f"\r{' ' * 90}\r", end="")
                        print(Fore.RED + Style.BRIGHT + 
                              f"  [VULNERABLE] {r['domain']} ➔ {r['provider']}")
                    else:
                        self._progress(done_tk, len(takeover_targets), "Hunting")
            
            if takeovers_found:
                self.log("w", f"\n[!] Found {len(takeovers_found)} potential takeovers!")
                with open(os.path.join(self.base_dir, "takeovers.txt"), "w") as f:
                    for tk in takeovers_found:
                        f.write(f"{tk['domain']} ➔ {tk['provider']}\n")
            else:
                self.log("s", "\n[+] No obvious takeovers found.")

        self._save_all(all_roots)
        self._stats(all_roots)

    def _save_all(self, all_roots):
        self.log("t", f"\n{'═' * 55}")
        self.log("t", " 💾  SAVING RESULTS")
        self.log("t", f"{'═' * 55}")
        p = os.path.join
        bd = self.base_dir

        with open(p(bd, "all_roots.txt"), "w") as f:
            for d in sorted(all_roots):
                f.write(d + "\n")
        self.log("s", f"  [✓] all_roots.txt  ({len(all_roots)})")

        with open(p(bd, "all_subdomains.txt"), "w") as f:
            for d in sorted(self.all_domains):
                f.write(d + "\n")
        self.log("s", f"  [✓] all_subdomains.txt  ({len(self.all_domains)})")

        everything = self.all_domains | all_roots
        with open(p(bd, "all_domains.txt"), "w") as f:
            for d in sorted(everything):
                f.write(d + "\n")
        self.log("s", f"  [✓] all_domains.txt  ({len(everything)})")

        if self.args.probe and self.live_results:
            with open(p(bd, "live_hosts.txt"), "w") as f:
                for r in sorted(self.live_results, key=lambda x: x["domain"]):
                    f.write(f"{r['proto']}://{r['domain']}\n")
            self.log("s", f"  [✓] live_hosts.txt  ({len(self.live_results)})")
            with open(p(bd, "live_detailed.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=[
                    "domain", "ip", "status", "proto", "server", "title"])
                w.writeheader()
                for r in sorted(self.live_results, key=lambda x: x["domain"]):
                    w.writerow({
                        "domain": r["domain"], "ip": r["ip"] or "",
                        "status": r["status"], "proto": r["proto"],
                        "server": r["server"] or "", "title": r["title"] or ""
                    })
            self.log("s", f"  [✓] live_detailed.csv")

        if self.acq_names:
            with open(p(bd, "acquisitions_list.txt"), "w") as f:
                for name in self.acq_names:
                    f.write(name + "\n")
            self.log("s", f"  [✓] acquisitions_list.txt  ({len(self.acq_names)})")

        report = {
            "target": self.args.target,
            "timestamp": datetime.now().isoformat(),
            "scan_time": round(time.time() - self.start_time, 1),
            "stats": {
                "total_subdomains": len(self.all_domains),
                "total_roots": len(all_roots),
                "acquisitions": len(self.acq_names),
                "live_hosts": len(self.live_results),
                "sources": self.sources_stats
            },
            "root_domains": {
                root: {"count": len(subs), "subdomains": sorted(subs)}
                for root, subs in sorted(self.all_subdomains.items())
            },
            "acquisitions": self.acquisitions,
            "live_results": self.live_results,
        }
        with open(p(bd, "report.json"), "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        self.log("s", f"  [✓] report.json")
        if os.path.isfile(self.state_file):
            os.remove(self.state_file)

    def _stats(self, all_roots):
        elapsed = time.time() - self.start_time
        self.log("t", f"\n{'═' * 55}")
        self.log("t", " 📊  STATISTICS")
        self.log("t", f"{'═' * 55}")
        print(Fore.WHITE + f"  Root domains     : {len(all_roots)}")
        print(Fore.WHITE + f"  Subdomains       : {len(self.all_domains)}")
        if self.live_results:
            print(Fore.WHITE + f"  Live hosts       : {len(self.live_results)}")
        if self.acq_names:
            print(Fore.WHITE + f"  Acquisitions     : {len(self.acq_names)}")
        print(Fore.WHITE + f"  Sources          :")
        for s, c in sorted(self.sources_stats.items()):
            print(Fore.WHITE + f"    • {s} : {c}")
        m, s = int(elapsed // 60), int(elapsed % 60)
        print(Fore.WHITE + f"  Time             : {m}m {s}s")
        self.log("t", f"\n{'═' * 55}")
        self.log("t", " 📂  OUTPUT STRUCTURE")
        self.log("t", f"{'═' * 55}")
        self._print_tree(self.base_dir)
        print(Fore.WHITE + "\n  Happy Hacking " + Fore.RED + ";)\n")

    def _print_tree(self, path, prefix="  ", depth=0):
        if depth > 2:
            return
        entries = sorted(os.listdir(path))
        entries = [e for e in entries if not e.startswith(".")]
        dirs = [e for e in entries if os.path.isdir(os.path.join(path, e))]
        files = [e for e in entries if os.path.isfile(os.path.join(path, e))]
        for f in files:
            size = os.path.getsize(os.path.join(path, f))
            size_str = f"{size}B" if size < 1024 else f"{size // 1024}KB"
            print(Fore.WHITE + f"{prefix}├── {f}" + Fore.CYAN + f"  ({size_str})")
        for d in dirs:
            sub_count = len([e for e in os.listdir(os.path.join(path, d))
                            if not e.startswith(".")])
            print(Fore.YELLOW + f"{prefix}├── {d}/" + Fore.CYAN + f"  ({sub_count} items)")
            if sub_count <= 5:
                self._print_tree(os.path.join(path, d), prefix + "│   ", depth + 1)


def main():
    banner()
    parser = argparse.ArgumentParser(
        description="crt digger v3.6",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=Fore.YELLOW + """
Examples:
  """ + Fore.WHITE + """%(prog)s "Microsoft"                      # Basic
  %(prog)s "Nokia"                          # Nokia won't be filtered!
  %(prog)s "Apple" -a                       # Apple won't be filtered!
  %(prog)s "Microsoft" -a -da               # Acquisitions + Level 2 (Deep Acq)
  %(prog)s "Microsoft" -a -g -d -p -tk      # Full scan with Takeover Hunting
  %(prog)s "Microsoft" --resume -p          # Resume
        """)

    parser.add_argument("target", help="Org / domain / file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-H", "--horizontal", action="store_true")
    mode.add_argument("-V", "--vertical", action="store_true")
    parser.add_argument("-a", "--acquisitions", action="store_true")
    parser.add_argument("-da", "--deep-acq", action="store_true", help="Discover Level 2 acquisitions (Acquisitions of Acquisitions)")
    parser.add_argument("-g", "--guess", action="store_true", help="Guess domains for acquisitions (may have false positives)")
    parser.add_argument("-d", "--deep", action="store_true")
    parser.add_argument("-p", "--probe", action="store_true")
    parser.add_argument("-tk", "--takeover", action="store_true", help="Check for potential Subdomain Takeovers")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("-t", "--threads", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("-h", "--help", action="help",
                        default=argparse.SUPPRESS)

    args = parser.parse_args()
    CrtDigger(args).run()


if __name__ == "__main__":
    main()

    with open(out_file, "w") as f:
        for d in sorted(final_domains): f.write(d + "\n")

    print(Fore.CYAN + f"\n[=] Saved to: {out_file}")
    print(Fore.WHITE + "Happy Hacking " + Fore.RED + ";)")

if __name__ == "__main__":
    main()
