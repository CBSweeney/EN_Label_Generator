# Product Label Generator

This Streamlit app generates product label PDFs with embedded GS1 DataMatrix barcodes.

## Features

- Upload your own PDF template
- Add product name, SKU, net weight, lot number, manufacture and expiry dates
- GS1-compliant barcode including AIs: GTIN (01), Net Weight (3103), MFG Date (11), EXP Date (17), Lot (10), COO (422), Manufacturer Location (91), Product Name (92), and SKU (21)
- Caches output to speed up duplicate requests

## Running Locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Deployment

Place your label template file in the same directory or upload it at runtime.
