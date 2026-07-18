import argparse, asyncio, json
from refassist.graphs import run_one

async def _run(args):
    out = await run_one(args.ref)
    print(out.get("formatted", ""))
    if args.verbose:
        print("\nReport:\n", out.get("report",""))
        print("\nCSL-JSON:\n", json.dumps(out.get("csl_json"), indent=2, ensure_ascii=False))
        print("\nBibTeX:\n", out.get("bibtex",""))

def main():
    p = argparse.ArgumentParser(description="RefAssist CLI")
    p.add_argument("--ref", required=True, help="Raw reference string")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    asyncio.run(_run(args))

if __name__ == "__main__":
    main()
