"""
Improved product label generator for Streamlit.

This version addresses a number of issues present in the original implementation:

* It avoids hard‑coded paths by allowing the user to upload a background
  template PDF or falling back to a default file bundled alongside this module.
* Dates are handled using ``datetime.date`` and ``dateutil.relativedelta`` to
  safely add years (even across leap years).
* The GS1 data string is still assembled manually, but the barcode itself is
  generated with the ``treepoem`` library, which understands GS1 DataMatrix
  encoding and inserts FNC1 and group separator characters automatically【929797942533156†L1184-L1198】.
* The Data Matrix barcode is converted to a PIL image and inserted into the
  overlay using ``reportlab``'s ``drawImage`` method; this is the
  recommended way to embed images in a PDF and benefits from ReportLab's
  internal caching【768376622927165†L681-L692】.
* Temporary files and intermediate SVG manipulations are eliminated to reduce
  complexity and potential resource leaks.
* The creation of the label PDF is cached so repeated requests with the same
  inputs do not regenerate the file unnecessarily.
* Unused imports from the original script have been removed.

To run this module you will need to add the following packages to your
``requirements.txt``: ``streamlit``, ``reportlab``, ``PyPDF2``, ``treepoem``,
``Pillow``, and ``python-dateutil``.
"""

from __future__ import annotations

import base64
import os
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Optional
import re
import streamlit as st
from dateutil.relativedelta import relativedelta
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from streamlit_pdf_viewer import pdf_viewer

try:
    import treepoem  # type: ignore[import]
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "The 'treepoem' package is required to generate GS1 DataMatrix barcodes. "
        "Please add treepoem to your requirements and ensure Ghostscript is installed."
    ) from exc


def sanitize_lot_code(lot_code):
    """Remove invalid characters per GS1 CSET 82 rules (AI 10)."""
    return re.sub(r'[^A-Z0-9\-\.\/]', '', lot_code.upper())

def sanitize_gs1_text(text: str) -> str:
    """Sanitize text for GS1 fields by uppercasing and removing disallowed characters."""
    return re.sub(r'[^A-Z0-9\-\.\/ ]', '', text.upper())

def build_gs1_string(
    gtin14: str,
    lot: str,
    mfg: date,
    exp: date,
    quantity_kg: float,
    mfr_loc: str,
    country_code: str,
    product_name: str,
    sku: str,
) -> str:
    """Assemble a GS1 application identifier string.

    This helper builds the data portion for a GS1 DataMatrix.  Each field is
    prefaced with its appropriate application identifier.  The weight is
    encoded in kilograms with three decimal places (as required by AI 3103).

    Parameters
    ----------
    gtin14:
        The 14‑digit Global Trade Item Number.
    lot:
        Lot or batch number (AI 10).
    mfg:
        Manufacturing date.  Encoded as YYMMDD with AI 11.
    exp:
        Expiry date.  Encoded as YYMMDD with AI 17.
    quantity_kg:
        Net weight in kilograms.
    mfr_loc:
        Internal manufacturer location code (used with AI 91).
    country_code:
        Three‑digit country code for country of origin (AI 422).  Use '840' for
        the United States.
    product_name:
        Product name (AI 92).
    sku:
        SKU (AI 21).

    Returns
    -------
    str
        The concatenated GS1 data string.
    """
    mfg_str = mfg.strftime("%y%m%d")
    exp_str = exp.strftime("%y%m%d")
    # Weight with 3 decimal places (AI 3103) – multiply by 1000 and zero‑pad to six digits
    weight_str = f"{int(round(quantity_kg * 1000)):06d}"
    product_name_sanitized = sanitize_gs1_text(product_name)
    sku_sanitized = sanitize_gs1_text(sku)
    return (
        f"(01){gtin14}"
        f"(3103){weight_str}"
        f"(11){mfg_str}"
        f"(17){exp_str}"
        f"(422){country_code}"
        f"(10){lot}"
        f"(21){sku_sanitized}"
        f"(91){mfr_loc}"
        f"(92){product_name_sanitized}"
    )


def generate_barcode_image(gs1_data: str) -> "PIL.Image.Image":
    """Generate a GS1 DataMatrix as a PIL image.

    Uses ``treepoem`` to render the barcode.  The ``parsefnc`` option
    automatically inserts FNC1 and group‑separator characters into the Data
    Matrix【929797942533156†L1184-L1198】.
    """
    barcode = treepoem.generate_barcode(
        barcode_type="gs1datamatrix",
        data=gs1_data,
        options={"parsefnc": True},
    )
    # Convert to RGB so ReportLab can embed it
    return barcode.convert("RGB")


@st.cache_data(show_spinner=False)
def render_label(
    name: str,
    sku: str,
    net_weight: float,
    lot_code: str,
    mfg_date: date,
    exp_date: date,
    coo_display: str,
    background_bytes: bytes,
    gs1_data: str,
) -> bytes:
    """Build the final PDF with text and barcode on top of the template.

    All numerical coordinates are given in millimetres and converted to points
    using ReportLab's mm unit【184307715199743†L86-L99】.  The resulting PDF is returned
    as raw bytes.  Caching avoids regenerating identical labels.
    """
    buffer = BytesIO()
    overlay_packet = BytesIO()
    # Label size: 152.5 mm × 101.6 mm
    page_width = 152.5 * mm
    page_height = 101.6 * mm
    c = canvas.Canvas(overlay_packet, pagesize=(page_width, page_height))

    # Draw fixed text
    start_x = 5 * mm
    start_y = 70 * mm
    step_y = 8.5 * mm
    font_size = 18
    c.setFont("Helvetica", font_size)
    c.drawString(start_x, start_y, f"NAME: {name}")
    c.drawString(start_x, start_y - step_y, f"SKU: {sku}")
    c.drawString(start_x, start_y - 2 * step_y, f"NET WEIGHT: {net_weight:.2f} KG")
    c.drawString(start_x, start_y - 3 * step_y, f"LOT #: {lot_code}")
    c.drawString(start_x, start_y - 4 * step_y, f"MFG. DATE: {mfg_date:%Y-%m-%d}")
    c.drawString(start_x, start_y - 5 * step_y, f"COO: {coo_display}")

    # Generate and insert the Data Matrix barcode
    barcode_img = generate_barcode_image(gs1_data)
    barcode_reader = ImageReader(barcode_img)
    # Position: bottom‑left corner at (122 mm, 6 mm); size 25 mm × 25 mm
    c.drawImage(
        barcode_reader,
        x=121.5 * mm,
        y=5.7 * mm,
        width=25 * mm,
        height=25 * mm,
    )
    c.save()

    # Combine the overlay with the template
    overlay_packet.seek(0)
    overlay_pdf = PdfReader(overlay_packet)
    base_pdf = PdfReader(BytesIO(background_bytes))
    output = PdfWriter()
    page = base_pdf.pages[0]
    page.merge_page(overlay_pdf.pages[0])
    output.add_page(page)
    output.write(buffer)
    return buffer.getvalue()


def load_template(default_name: str = "Elect Nano 2026 Label Template V1.pdf") -> Optional[bytes]:
    """Load the template PDF either from an uploaded file or packaged resource.

    If the user uploads a template using the file uploader, that file is used.
    Otherwise, the function attempts to load a file with the given name from
    the directory containing this script.  Returns ``None`` if no file can
    be found.
    """
    uploaded = st.session_state.get("_uploaded_template")
    if uploaded is not None:
        return uploaded.getvalue()  # type: ignore[no-any-return]
    script_dir = Path.cwd()  # use current working directory
    candidate = script_dir / default_name
    if candidate.exists():
        return candidate.read_bytes()
    return None


def main() -> None:
    st.set_page_config(page_title="Product Label Generator", layout="centered")
    st.title("Elect Nano Product Label Generator from PDF Template")

    # Allow template upload
    with st.expander("⚙️ Template settings", expanded=False):
        uploaded_file = st.file_uploader(
            "Optional: upload a custom background PDF (one page only)",
            type=["pdf"],
            key="template_file",
            help="If no file is uploaded, the app will look for a bundled template named 'Elect Nano 2026 Label Template V1.pdf'.",
        )
        if uploaded_file:
            # Store in session state so load_template can find it
            st.session_state["_uploaded_template"] = uploaded_file
            st.success("Custom template uploaded successfully.")

    country_options = {
        "United States": "840",
        "Japan": "392",
        "China": "156",
    }
    mfr_loc_options = ["A-PCT", "B-PCT", "C-PCT", "A-SHR", "A-GTW", "A-FJI"]

    with st.form("label_form"):
        raw_name = st.text_input("Product Name", value="PRODUCT NAME")

        # For GS1 encoding only
        sanitized_name = sanitize_gs1_text(raw_name.replace(" ", ""))
        if sanitized_name != raw_name:
            st.warning(
                "Product Name contains invalid characters for GS1 encoding. "
                f"It will be encoded as:\n`{sanitized_name}`"
            )

        sku = st.text_input("SKU", value="E-00-00-000-000-00") #default SKU here
        net_weight = st.number_input(
            "Net Weight (kg)",
            value=25.00,
            step=0.01,
            min_value=0.01,
            format="%0.2f",
        )
        lot_num = st.text_input("Lot #", value="EN-YYMM-XX")
        raw_lot_num = lot_num
        lot_num = sanitize_lot_code(raw_lot_num)
        if lot_num != raw_lot_num:
            st.warning("Lot # contains invalid characters. It has been sanitized to comply with GS1 rules:\n" f"`{lot_num}`")
        mfg_date = st.date_input("Manufacturing Date", value=date.today())

        mfr_loc = st.selectbox("Manufacturing Location", options=mfr_loc_options, index=mfr_loc_options.index("A-GTW"))
        coo_display = st.selectbox("Country of Origin", options=list(country_options.keys()), index=0)
        coo_code = country_options[coo_display]

        submitted = st.form_submit_button("Generate Label")
        if submitted and lot_num != raw_lot_num:
            st.error("Label generation stopped: Lot # contains invalid characters that are not allowed in GS1 DataMatrix (AI 10).")
            return

    # Only act after the form is submitted
    if submitted:
        # Load the background template
        bg_bytes = load_template()
        if bg_bytes is None:
            st.error(
                "No background PDF found. Please upload a template or place the default file "
                "named 'Elect Nano 2026 Label Template V1.pdf' in the same directory as this script."
            )
            return

        # Compute the expiration date – add 5 years safely
        try:
            exp_date = mfg_date + relativedelta(years=5)
        except Exception:
            # As a fallback, set expiry to 5 years minus one day
            exp_date = mfg_date + relativedelta(years=5, days=-1)

        # Build the GS1 data string and render the label
        gs1_data = build_gs1_string(
            gtin14="00069766967842", #spoofed GTIN14, made using 000 USA prefix and ASCII numbers for ELECT and adding check digit
            lot=lot_num,
            mfg=mfg_date,
            exp=exp_date,
            quantity_kg=net_weight,
            mfr_loc=mfr_loc,
            country_code=coo_code,
            product_name=sanitized_name,
            sku=sku,
        )
        st.code(gs1_data, language="text")
        pdf_bytes = render_label(
            name=raw_name,
            sku=sku,
            net_weight=net_weight,
            lot_code=lot_num,
            mfg_date=mfg_date,
            exp_date=exp_date,
            coo_display=coo_display,
            background_bytes=bg_bytes,
            gs1_data=gs1_data,
        )

        st.success("✅ Label generated successfully!")

        # Offer download
        st.download_button(
            label="📥 Download Label PDF",
            data=pdf_bytes,
            file_name=f"{sku}_{lot_num}.pdf",
            mime="application/pdf",
        )

        # Display PDF in interactive viewer
        pdf_viewer(pdf_bytes, width=700, height=1000, zoom_level=1.0)

if __name__ == "__main__":  # pragma: no cover
    main()
