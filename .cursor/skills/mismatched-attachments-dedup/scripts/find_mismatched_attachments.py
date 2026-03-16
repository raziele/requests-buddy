#!/usr/bin/env python3
"""
Scan request folders and find files that don't relate to the request described in the md file.
Uses filename patterns to identify which organization each file belongs to.

Usage:
    python find_mismatched_attachments.py           # Report only (default)
    python find_mismatched_attachments.py --fix --dry-run   # Show what would be deleted
    python find_mismatched_attachments.py --fix     # Delete misplaced files

Run from project root, or use --requests-dir to specify path to requests-buddy/requests.
"""

import argparse
import re
from pathlib import Path


def _find_requests_dir() -> Path:
    """Find requests-buddy/requests by walking up from script location."""
    p = Path(__file__).resolve().parent
    for _ in range(10):
        candidate = p / "requests-buddy" / "requests"
        if candidate.is_dir():
            return candidate
        # Script inside requests-buddy/scripts/
        if (p / "requests").is_dir():
            return p / "requests"
        if p.parent == p:
            break
        p = p.parent
    raise FileNotFoundError(
        "Could not find requests-buddy/requests. Run from project root or use --requests-dir."
    )


# Exclusions: (folder_pattern, filename_pattern) - do not flag or delete
EXCLUSIONS = [
    # Roaring Lion Portfolio is correctly placed - Association for Israel's Soldiers' Emergency Relief Fund portfolio
    ("the-association-for-israel-s-soldiers", "roaring lion portfolio"),
    # Combined bulletin: Lasova + Kadima; both folders legitimately have it
    ("kadima-youth-homes", "לשובע"),
]

# Filename patterns -> organization they belong to (lowercase for matching)
FILE_TO_ORG = {
    "ezrat achim": ["ezrat achim", "beit shemesh emergency", "beit shemesh appeal"],
    "elah center": ["elah", "מרכז אלה", "proposal for a program of support and supervision for social workers", "עובדי רווחה"],
    "hrcc": ["hrcc", "haifa rape crisis", "shaagathaari"],
    "al-bachur": ["אלבאכור", "al-bachur", "al bachur"],
    "access israel": ["access israel", "נגישות ישראל", "אפוד סגול", "purple vest"],
    "zaka": ["zaka", "national emergency preparedness"],
    "mechinot": ["mechinot", "readiness for emergency"],
    "pink glasses": ["pink glasses", "משקפיים ורודים"],
    "niroot": ["niroot", "food vouchers"],
    "or movement": ["or movement", "aid_request_jfna"],
    "eran": ["eran", "emotional first aid"],
    "mashiv haruach": ["mashiv", "משיב הרוח", "expanding our capacity to heal", "frontline caregivers", "metsoke dragot", "מצוקי דרגות"],
    "nitzan": ["nitzan for the idf", "nitzan for reservists"],
    "dror israel": ["dror israel", "sheagat haari"],
    "lasova": ["לשובע", "lasova"],
    "galilee medical": ["galilee medical"],
    "jdc": ["jdc", "joint distribution"],
    "ivc": ["ivc", "israeli volunteering council", "emergency kits for children in arab"],
    "rambam": ["rambam", "lion's roar needs"],
    "summit": ["מכון סאמיט", "summit institute"],
    "latet": ["latet"],
    "kibbutz movement": ["kibbutz movement", "kibbutz communities"],
    "early starters": ["early starters", "proposal_emergency update"],
    "kibbutz manara": ["מנארה", "manara", "צרכים למקלטים", "כיתת כוננות"],
    "nifgashim": ["nifgashim"],
    "elem": ["elem", "emergency activities"],
    "united hatzalah": ["united hatzalah"],
    "haifa municipality": ["תוכניות עיריית חיפה"],
    "hamal ezrachi": ["hamal ezrachi", "calm in the shelter"],
    "project kesher": ["project kesher"],
    "druzetch": ["druzetch", "druze", "הקמת אקסלרטור"],
    "kedma": ["kedma"],
    "al-amal": ["alamal", "al-amal"],
    "beit issie": ["beit issie", "issie shapiro"],
    "journey4hope": ["j4h", "journey 4 hope", "bereaved mothers"],
    "israel women's network": ["alice", "alice hotline", "alice line"],
    "brothers and sisters": ["hamal ezrachi", "calm in the shelter"],
    "jewish federations": ["emergency educational response during operation", "roaring lion portfolio"],
    "the jewish agency": ["operation roaring lion", "jewish agency"],
    "tel aviv sexual assault": ["tel aviv sexual assault"],
}


def normalize(s: str) -> str:
    """Normalize for matching: lowercase, collapse spaces."""
    return " ".join(s.lower().split())


def get_org_from_filename(filename: str) -> str | None:
    """Return organization name if filename clearly indicates one."""
    name = normalize(filename)
    for org, patterns in FILE_TO_ORG.items():
        for p in patterns:
            if p in name:
                return org
    return None


def extract_org_from_md(content: str, folder_name: str = "") -> str:
    """Extract organization name from md content."""
    m = re.search(r"\*\*Organization\*\*\s*\|\s*([^|]+)", content)
    if m:
        raw = m.group(1).strip()
        if raw.upper() in ("RE:", "RE", "—", "-") or len(raw) < 3:
            raw = ""
        else:
            raw = re.split(r"\s*[/(]|\s*—", raw)[0].strip()
            raw = normalize(raw)
    if not raw and folder_name:
        parts = folder_name.replace("_", "-").split("-")
        skip = {"emergency", "aid", "funding", "grant", "program", "response", "support", "roar", "lion", "roaring"}
        words = [p for p in parts if p.lower() not in skip and len(p) > 1][:3]
        raw = " ".join(words).lower() if words else ""
    return raw or ""


def extract_attachments_from_md(content: str) -> set[str]:
    """Extract listed attachment filenames from Attachments table."""
    attachments = set()
    in_table = False
    for line in content.splitlines():
        if "| Filename |" in line or "| filename |" in line.lower():
            in_table = True
            continue
        if in_table and line.strip().startswith("|") and "|---" not in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2 and parts[1]:
                attachments.add(parts[1])
        elif in_table and line.strip() and not line.strip().startswith("|"):
            break
    return attachments


def is_excluded(folder_path: str, filename: str) -> bool:
    """Return True if this mismatch should be excluded (false positive)."""
    folder_lower = folder_path.lower()
    name_lower = filename.lower()
    for folder_pattern, filename_pattern in EXCLUSIONS:
        if folder_pattern in folder_lower and filename_pattern in name_lower:
            return True
    return False


def orgs_match(folder_org: str, file_org: str) -> bool:
    """Check if file org matches folder org (fuzzy)."""
    if not folder_org or not file_org:
        return True
    fo = folder_org.replace("-", " ").replace("'", "").strip()
    fa = file_org.replace("-", " ").replace("'", "").strip()
    if fa in fo or fo in fa:
        return True
    aliases = {
        "ezrat achim": ["ezrat achim"],
        "elah center": ["elah", "merkaz elah", "מרכז אלה"],
        "haifa rape crisis center": ["hrcc", "haifa rape crisis"],
        "al-bachur": ["al bachur", "אלבאכור"],
        "access israel": ["נגישות ישראל"],
        "the joint council of the mechinot": ["mechinot", "mechinot council"],
        "mashiv haruach": ["mashiv haruach", "משיב הרוח"],
        "nitzan association": ["nitzan"],
        "nitzan": ["nitzan"],
        "association for the advancement of the druze soldier": ["druzetch", "druze"],
        "brothers and sisters for israel": ["hamal ezrachi", "hamal ezrachi bsi"],
        "israeli volunteering council": ["ivc"],
        "or movement for the negev and galilee": ["or movement"],
        "niroot forum of ngos for young adults at risk": ["niroot"],
        "the jewish agency": ["roaring lion", "operation roaring lion", "jewish agency"],
        "jewish federations": ["jewish federations", "emergency educational response"],
    }
    for key, vals in aliases.items():
        if key in fo or fo in key:
            if fa in vals or any(v in fa for v in vals):
                return True
    return False


def scan_mismatches(requests_dir: Path):
    results = []
    for md_path in requests_dir.rglob("*.md"):
        folder = md_path.parent
        folder_name = folder.name
        content = md_path.read_text(encoding="utf-8", errors="replace")
        folder_org = extract_org_from_md(content, folder_name)
        listed_attachments = extract_attachments_from_md(content)

        for f in folder.iterdir():
            if f.is_dir() or f.name == md_path.name:
                continue
            if f.suffix.lower() in (".md",):
                continue

            file_org = get_org_from_filename(f.name)
            if file_org and not orgs_match(folder_org, file_org):
                folder_rel = str(folder.relative_to(requests_dir))
                if not is_excluded(folder_rel, f.name):
                    results.append({
                        "folder": folder_rel,
                        "folder_path": folder,
                        "file": f.name,
                        "file_path": f,
                        "file_suggests_org": file_org,
                        "folder_org": folder_org,
                        "in_attachments_list": f.name in listed_attachments,
                    })

    return results


def main():
    parser = argparse.ArgumentParser(description="Find and optionally remove misplaced attachments")
    parser.add_argument("--fix", action="store_true", help="Remove misplaced files (use with --dry-run first)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    parser.add_argument("--requests-dir", type=Path, help="Path to requests-buddy/requests (default: auto-detect)")
    args = parser.parse_args()

    requests_dir = args.requests_dir or _find_requests_dir()
    if not requests_dir.is_dir():
        print(f"Error: {requests_dir} is not a directory")
        return 1

    results = scan_mismatches(requests_dir)

    print("=" * 80)
    print("FILES THAT APPEAR TO BE IN THE WRONG REQUEST FOLDER")
    print("(Filename suggests a different organization than the request)")
    print("=" * 80)

    by_folder = {}
    for r in results:
        key = r["folder"]
        if key not in by_folder:
            by_folder[key] = []
        by_folder[key].append(r)

    for folder in sorted(by_folder.keys()):
        items = by_folder[folder]
        print(f"\n📁 {folder}")
        print(f"   Request org: {items[0]['folder_org']}")
        for r in items:
            print(f"   ❌ {r['file']}")
            print(f"      → Suggests: {r['file_suggests_org']}")

    print("\n" + "=" * 80)
    print(f"Total: {len(results)} potentially misplaced file(s) across {len(by_folder)} folder(s)")
    print("=" * 80)

    if args.fix and results:
        if args.dry_run:
            print("\n[DRY RUN] Would delete the following files:")
            for r in results:
                print(f"  {r['file_path']}")
        else:
            deleted = 0
            for r in results:
                p = r["file_path"]
                if p.exists():
                    p.unlink()
                    print(f"Deleted: {p}")
                    deleted += 1
            print(f"\nDeleted {deleted} file(s).")
    elif args.fix and not results:
        print("\nNo misplaced files to fix.")

    return 0


if __name__ == "__main__":
    exit(main())
