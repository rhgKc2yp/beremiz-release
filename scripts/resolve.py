#!/usr/bin/env python3
"""Resolve official release sources and upstream's dependency revisions."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from build_paths import workspace, output_path

RECIPE = "d80eb660b83473df8e767f28f6980ecbaa9e1024"
# Last 0.8 release: Beremiz's BACnet generator uses the legacy flat headers/API.
BACNET_REVISION = "0af5a273304a4ecb5215c7e3fe473f527dcf0079"  # 0.8.7


TAG = r"v[0-9]+(?:\.[0-9]+)+"
COMMIT = r"[0-9a-f]{40}"
REPOS = r"(?:beremiz/(?:beremiz|matiec|Modbus|canfestival)|open62541/open62541|bacnet-stack/bacnet-stack)"
ALLOWED_PATHS = {
    "api.github.com": (
        rf"/repos/beremiz/beremiz/releases/(?:latest|tags/{TAG})",
        rf"/repos/beremiz/beremiz/commits/{TAG}",
    ),
    "raw.githubusercontent.com": (
        rf"/beremiz/beremiz_public_dist/{RECIPE}/\.github/workflows/prep_sources\.yml",
    ),
    "codeload.github.com": (rf"/{REPOS}/tar\.gz/{COMMIT}",),
}


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc not in ALLOWED_PATHS
            or parsed.query or parsed.fragment
            or not any(re.fullmatch(pattern, parsed.path)
                       for pattern in ALLOWED_PATHS[parsed.netloc])
            or url != f"https://{parsed.netloc}{parsed.path}"):
        raise ValueError("Download URL is outside the approved GitHub endpoints")
    return parsed.netloc


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward credentials or requests to a redirect target.
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def get(url):
    host = validate_url(url)
    headers = {"User-Agent": "beremiz-community-release"}
    if host == "api.github.com" and os.getenv("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    with urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(url, headers=headers), timeout=120) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="")
    parser.add_argument("--revision", default="1")
    args = parser.parse_args()
    if not re.fullmatch(r"[1-9][0-9]*", args.revision):
        parser.error("revision must be a positive integer")
    if args.tag and not re.fullmatch(TAG, args.tag):
        parser.error("tag must be a stable version such as v1.6.0")
    endpoint = "tags/" + args.tag if args.tag else "latest"
    release = json.loads(get("https://api.github.com/repos/beremiz/beremiz/releases/" + endpoint))
    tag = release["tag_name"]
    if release["draft"] or release["prerelease"] or not re.fullmatch(TAG, tag):
        raise SystemExit("Only stable upstream version tags are accepted")
    recipe = get(f"https://raw.githubusercontent.com/beremiz/beremiz_public_dist/{RECIPE}/.github/workflows/prep_sources.yml").decode()
    pins = dict(re.findall(r"^\s*(\w+)_revisionid:\s*([0-9a-f]{40})", recipe, re.M))
    sources = {"beremiz": ("beremiz/beremiz", tag),
               "matiec": ("beremiz/matiec", pins["matiec"]),
               "Modbus": ("beremiz/Modbus", pins["Modbus"]),
               "canfestival": ("beremiz/canfestival", pins["canfestival"]),
               "open62541": ("open62541/open62541", pins["open62541"]),
               "BACnet": ("bacnet-stack/bacnet-stack", BACNET_REVISION)}
    root = workspace("sources")
    root.mkdir(parents=True, exist_ok=True)
    records = {}
    for name, (repo, ref) in sources.items():
        commit = json.loads(get(f"https://api.github.com/repos/{repo}/commits/{ref}"))["sha"] if name == "beremiz" else ref
        if not isinstance(commit, str) or not re.fullmatch(COMMIT, commit):
            raise SystemExit("Invalid upstream commit SHA")
        url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
        archive = output_path(root, name + ".tar.gz")
        archive.write_bytes(get(url))
        destination = output_path(root, name)
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir()
        subprocess.run(["tar", "--no-same-owner", "--strip-components=1", "-xzf", str(archive), "-C", str(destination)], check=True)
        records[name] = {"repository": repo, "commit": commit, "url": url,
                         "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    lock = {"tag": tag + "-" + args.revision, "upstream_tag": tag,
            "release_url": release["html_url"], "release_date": release["published_at"][:10],
            "recipe_commit": RECIPE, "runtime": "org.gnome.Platform//51",
            "sdk": "org.gnome.Sdk//51", "sources": records}
    workspace("inputs.lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    print(lock["tag"])


if __name__ == "__main__":
    main()
