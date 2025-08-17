from IPython.display import display, Markdown, SVG, HTML
try:
    import httpx
except Exception:
    httpx=None

MERMAID_DAG = r'''flowchart TD
A[Init Runtime] --> B[Detect Type (Heuristics + LLM)]
B --> C[Parse & Extract (LLM-first)]
C --> D[Fetch Candidates (Crossref/OpenAlex/S2/PubMed/arXiv)]
D --> E[Select Best (Consensus Scoring)]
E --> F[Verification Agents + Guards]
F -->|repair| G[Apply Corrections]
G --> I[LLM Correction]
I --> X[Enrich From Best]
X --> D2[Re-Fetch Candidates]
D2 --> E2[Re-Select Best]
E2 --> F2[Re-Verify + loop/stagnation guards]
F -->|exit| H[Format IEEE]
H --> J[Build CSL-JSON & BibTeX]
J --> R[Human Report]
style H fill:#e0f7fa,stroke:#006064,stroke-width:1px
style R fill:#f1f8e9,stroke:#33691e,stroke-width:1px
style D fill:#fff3e0,stroke:#e65100,stroke-width:1px
style F fill:#ede7f6,stroke:#4527a0,stroke-width:1px
'''

def show_mermaid_inline(mermaid_code: str) -> None:
    html = f"""
    <div class="mermaid">{mermaid_code}</div>
    <script>
      (function() {{
        function init() {{ mermaid.initialize({{startOnLoad:true}}); }}
        if (!window.mermaid) {{
          var s = document.createElement('script');
          s.src = 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js';
          s.onload = init; document.head.appendChild(s);
        }} else {{ init(); }}
      }})();
    </script>
    """
    display(HTML(html))

def show_mermaid_kroki(mermaid_code: str) -> None:
    display(Markdown(f"\n```mermaid\n{mermaid_code}\n```\n"))
    if httpx is None: return
    try:
        r = httpx.post("https://kroki.io/mermaid/svg", content=mermaid_code.encode("utf-8"), timeout=10.0,
                       headers={"Content-Type":"text/plain"})
        if r.status_code == 200: display(SVG(r.content))
    except Exception:
        ...
