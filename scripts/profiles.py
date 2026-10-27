#!/usr/bin/env python3
# Makes the Ember pages for my XL and my + as bundled profiles, in the
# same format Stream Deck itself exports: a .streamDeckProfile is a zip of
# a <uuid>.sdProfile folder. The shape is copied from the profiles script
# in my Mac Vitals and Apple Music plugins, which got it from a real
# export. Those are JavaScript. This plugin is Python, so this is too.
#
# Each page's Home key points at my own Home profile for that deck, found
# on this Mac. Those ids only mean something here, so the files this
# writes are ignored by git and made again with "python3 scripts/profiles.py".
#
# Stream Deck installs a bundled profile once, so a changed layout only
# arrives after the plugin is removed and put back (or the file is opened
# by hand, which is also the only way a copied-in plugin ever gets it).
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

PLUGIN_DIR = Path(__file__).parents[1] / "com.embermug.dial.sdPlugin"
PROFILES = Path.home() / "Library/Application Support/com.elgato.StreamDeck/ProfilesV3"
# The date every file inside the zip is stamped with. A zip remembers when
# each file was changed, so "just now" would make every build look new.
BUILT = (2026, 10, 27, 12, 0, 0)

# Stream Deck's own Switch Profile action, pointed at that deck's Home, so
# the page always has a way back. Its picture is Stream Deck's own white
# house on pure black (home.svg, next to this file).
SWITCH_PROFILE = "com.elgato.streamdeck.profile.rotate"
HOME_IMAGE = "home.png"

# Each page: the model name Stream Deck writes into that deck's profiles
# (read from profiles on my Mac), where Home goes, the keys as (column,
# row, action, settings) and the dials as (column, action), counting from
# 0 at the top left.
#
# The XL has everything along the top row like my Mac Vitals XL page.
# The + has On/Off top left, Home top right where it is on my other +
# pages, the four temps underneath, and the Ember dial on the first dial.
TEMPS = [("temp", {"temp": str(temp)}) for temp in (120, 130, 140, 145)]
PAGES = {
    "ember-xl": {
        "name": "Ember XL",
        "model": "20GAT9901",
        "home": (0, 0),
        "keys": [(1, 0, "power", {})] + [(2 + i, 0, *temp) for i, temp in enumerate(TEMPS)],
        "dials": [],
    },
    "ember-plus": {
        "name": "Ember +",
        "model": "20GBD9901",
        "home": (3, 0),
        "keys": [(0, 0, "power", {})] + [(i, 1, *temp) for i, temp in enumerate(TEMPS)],
        "dials": [(0, "temperature")],
    },
}


def uuid_from(name):
    # Ids made from a name, not from chance, so building twice gives the
    # same ids.
    hex_ = hashlib.sha1(f"ember-mug:{name}".encode()).hexdigest()
    return f"{hex_[:8]}-{hex_[8:12]}-4{hex_[13:16]}-a{hex_[17:20]}-{hex_[20:32]}".upper()


def entry(manifest, action_name, settings, id_, dial=False):
    """One key or dial, the way Stream Deck writes it into a profile."""
    base = {"ActionID": uuid_from(id_).lower(), "LinkedTitle": False, "Resources": None, "Settings": settings, "State": 0}
    # Keys draw their own words, so Stream Deck's title stays hidden on
    # them. On a dial, the strip's top line is the layout item called
    # "title", and ShowTitle false hides it too: the dial showed 135° with
    # nothing above it.
    state = {"FontFamily": "", "FontSize": 12, "FontStyle": "", "FontUnderline": False, "OutlineThickness": 2, "ShowTitle": dial, "TitleAlignment": "bottom", "TitleColor": "#ffffff"}
    if action_name == "home":
        return {
            **base,
            "Name": "Switch Profile",
            "Plugin": {"Name": "Switch Profile", "UUID": SWITCH_PROFILE, "Version": "1.0"},
            "States": [{**state, "Image": f"Images/{HOME_IMAGE}"}],
            "UUID": SWITCH_PROFILE,
        }
    uuid = f"{manifest['UUID']}.{action_name}"
    action = next((a for a in manifest["Actions"] if a["UUID"] == uuid), None)
    if action is None:
        raise ValueError(f"No action {action_name} in the manifest")
    return {
        **base,
        "Name": action["Name"],
        "Plugin": {"Name": manifest["Name"], "UUID": manifest["UUID"], "Version": manifest["Version"]},
        "States": [state],
        "UUID": uuid,
    }


def profile_files(name, manifest, home):
    """The files inside one profile, as {path: object}."""
    page_layout = PAGES[name]
    root = f"{uuid_from(f'{name}:profile')}.sdProfile"
    page = uuid_from(f"{name}:page")
    # Stream Deck's own exports carry an empty default page that isn't in
    # the page list. I do the same.
    default_page = uuid_from(f"{name}:default")
    empty = {"Controllers": [{"Actions": None, "Type": "Keypad"}, {"Actions": None, "Type": "Encoder"}], "Icon": "", "Name": ""}
    keys = {f"{column},{row}": entry(manifest, action, settings, f"{name}:{column},{row}") for column, row, action, settings in page_layout["keys"]}
    if home:
        column, row = page_layout["home"]
        keys[f"{column},{row}"] = entry(manifest, "home", {"DeviceUUID": "", "PageIndex": 1, "ProfileUUID": home}, f"{name}:{column},{row}")
    dials = {f"{column},0": entry(manifest, action, {}, f"{name}:dial:{column}", dial=True) for column, action in page_layout["dials"]}
    return {
        f"{root}/manifest.json": {
            "Device": {"Model": page_layout["model"], "UUID": ""},
            "Name": page_layout["name"],
            "Pages": {"Current": page.lower(), "Default": default_page.lower(), "Pages": [page.lower()]},
            "Version": "3.0",
        },
        f"{root}/Profiles/{default_page}/manifest.json": empty,
        f"{root}/Profiles/{page}/manifest.json": {**empty, "Controllers": [{"Actions": keys, "Type": "Keypad"}, {"Actions": dials or None, "Type": "Encoder"}]},
    }


def find_home(model):
    # My Home profile for one kind of deck among Stream Deck's own saved
    # profiles: the one named "Home" for that model. Its id is the folder's
    # name in lowercase.
    for folder in sorted(PROFILES.glob("*.sdProfile")):
        try:
            saved = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if saved.get("Name") == "Home" and saved.get("Device", {}).get("Model") == model:
            return folder.stem.lower()
    return None


def write_profile(out, files, home):
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zip_:
        # Just the files, profile manifest first and no folder entries, the
        # way Stream Deck's own exports are laid out.
        for path, content in files.items():
            zip_.writestr(zipfile.ZipInfo(path, BUILT), json.dumps(content))
        if home:
            # A key's picture lives in an Images folder beside the page it's
            # on, and has to be a PNG. sips comes with macOS and converts it.
            png = Path(work) / HOME_IMAGE
            subprocess.run(["sips", "-s", "format", "png", str(Path(__file__).with_name("home.svg")), "--out", str(png)], check=True, capture_output=True, timeout=30)
            page_path = next(path for path, content in files.items() if content.get("Controllers", [{}])[0].get("Actions"))
            zip_.writestr(zipfile.ZipInfo(str(Path(page_path).parent / "Images" / HOME_IMAGE), BUILT), png.read_bytes())


if __name__ == "__main__":
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    for name, page_layout in PAGES.items():
        home = find_home(page_layout["model"])
        write_profile(PLUGIN_DIR / f"profiles/{name}.streamDeckProfile", profile_files(name, manifest, home), home)
        print(f"Wrote com.embermug.dial.sdPlugin/profiles/{name}.streamDeckProfile")
        print(f"  Home for {page_layout['name']}: {'found' if home else 'not found, so the page has no Home key'}")
