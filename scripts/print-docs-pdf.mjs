// Print the combined print-site page to a single PDF with headless Chromium.
//
// Why this is not a plain "load page and print": Material renders Mermaid client-side,
// and its bundled runtime does not reliably render the diagrams in headless Chromium
// (it consumes the <pre class="mermaid"><code> source and leaves empty divs). So we
// block Material's own JS and render every diagram ourselves from the static source
// with a pinned Mermaid build, which is deterministic. Material's CSS still loads, so
// theme/tables/admonitions/code styling are intact; the @media print rules in
// extra.css keep wide diagrams, tables, and code from being clipped.
//
// Usage: node print-docs-pdf.mjs <url> <output.pdf>

import puppeteer from "puppeteer";

const MERMAID_URL = "https://unpkg.com/mermaid@11.15.0/dist/mermaid.esm.min.mjs";

const url = process.argv[2];
const out = process.argv[3] || "orcastra-docs.pdf";
if (!url) {
  console.error("usage: node print-docs-pdf.mjs <url> <output.pdf>");
  process.exit(1);
}

const browser = await puppeteer.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
  defaultViewport: { width: 1280, height: 1600 },
});

try {
  const page = await browser.newPage();

  // Block Material's JS (and print-site's) so it cannot strip the static Mermaid source.
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const u = req.url();
    if (/\/assets\/javascripts\/bundle\.[^/]*\.js/.test(u) || /\/js\/print-site\.js/.test(u)) {
      req.abort();
    } else {
      req.continue();
    }
  });
  page.on("pageerror", (e) => console.log("[pageerror]", e.message.slice(0, 160)));

  await page.goto(url, { waitUntil: "load", timeout: 120000 });

  // Render every Mermaid block ourselves from its static source.
  const result = await page.evaluate(async (mermaidUrl) => {
    const mod = await import(mermaidUrl);
    const mermaid = mod.default;
    mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });
    const els = [...document.querySelectorAll(".mermaid")];
    const errs = [];
    let ok = 0;
    for (let i = 0; i < els.length; i++) {
      const src = els[i].textContent.trim();
      try {
        const { svg } = await mermaid.render("mmd" + i, src);
        els[i].innerHTML = svg;
        ok++;
      } catch (e) {
        errs.push(String(e).slice(0, 120));
      }
    }
    return { total: els.length, ok, svgs: document.querySelectorAll(".mermaid svg").length, errs };
  }, MERMAID_URL);

  console.log(`mermaid: ${result.ok}/${result.total} rendered (svg=${result.svgs})`);
  if (result.errs.length) console.log("mermaid errors:", result.errs);

  // Guard: never publish a PDF with broken diagrams.
  if (result.total > 0 && result.ok < result.total) {
    throw new Error(`only ${result.ok}/${result.total} Mermaid diagrams rendered`);
  }

  await page.evaluate(() => document.fonts && document.fonts.ready);

  await page.pdf({
    path: out,
    preferCSSPageSize: true, // honor @page from the print stylesheet
    printBackground: true,
    displayHeaderFooter: false,
    timeout: 120000,
  });
  console.log(`wrote ${out}`);
} finally {
  await browser.close();
}
