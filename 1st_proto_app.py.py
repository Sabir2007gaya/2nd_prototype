import io
import re
import numpy as np
import pandas as pd
import streamlit as st

# Optional imports
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except Exception:
    HAS_FITZ = False

try:
    from fpdf import FPDF
    HAS_FPDF = True
except Exception:
    HAS_FPDF = False

# ---------- Page Config ----------
st.set_page_config(
    page_title="🧠 Intelli‑Credit Prototype",
    layout="wide",
    page_icon="💼"
)

# ---------- Custom CSS ----------
st.markdown("""
<style>
    .main {
        background-color: #f9fafc;
        color: #1e1e1e;
        font-family: 'Segoe UI', sans-serif;
    }
    h1, h2, h3 {
        color: #004aad;
    }
    .stMetric {
        background-color: #ffffff;
        border-radius: 10px;
        padding: 10px;
        box-shadow: 0px 1px 3px rgba(0,0,0,0.1);
    }
    .stDownloadButton button {
        background-color: #004aad !important;
        color: white !important;
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)

# ---------- Helper Functions ----------
def extract_text_from_pdf(uploaded_file):
    if uploaded_file is None:
        return ""
    data = uploaded_file.read()
    if HAS_FITZ:
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            text = "".join(page.get_text() for page in doc)
            return text
        except Exception:
            return "⚠️ Error while parsing PDF. Please check the file."
    return "PDF extraction not available. Run locally with PyMuPDF."

def find_number_after_keyword(text, keywords):
    text_lower = text.lower()
    lines = text_lower.splitlines()
    for line in lines:
        for kw in keywords:
            if kw.lower() in line:
                nums = re.findall(r"[0-9,]+\.?[0-9]*", line)
                vals = [float(n.replace(",", "")) for n in nums if n]
                if vals:
                    return max(vals)
    return None

def compute_gst_bank_flags(gst_df, bank_df):
    if gst_df is None or bank_df is None:
        return None, None, False
    required_cols_gst = {"month", "gst_sales"}
    required_cols_bank = {"month", "credits_from_sales"}
    if not required_cols_gst.issubset(gst_df.columns) or not required_cols_bank.issubset(bank_df.columns):
        return None, None, False
    merged = gst_df.merge(bank_df, on="month", how="inner")
    if merged.empty:
        return merged, None, False
    merged["delta"] = (merged["gst_sales"] - merged["credits_from_sales"]) / merged["gst_sales"].replace(0, np.nan)
    circular_flag = merged["delta"].abs().fillna(0) > 0.3
    return merged, circular_flag, circular_flag.any()

def detect_litigation_flag(text):
    keywords = ["litigation", "dispute", "penalty", "show cause", "nclt", "tribunal", "suit"]
    hits = sum(text.lower().count(k) for k in keywords)
    return hits > 3, hits

def compute_score(features):
    score = 50
    d_to_e = features.get("d_to_e")
    rev_growth = features.get("rev_growth")
    if d_to_e is not None:
        score += 10 if d_to_e < 1 else 5 if d_to_e <= 2 else -10
    if rev_growth is not None:
        score += 10 if rev_growth > 10 else 5 if rev_growth >= 0 else -10
    if features.get("circular_flag"): score -= 15
    if features.get("litigation_flag"): score -= 15
    return max(0, min(100, score))

def map_decision(score):
    return "Reject" if score < 40 else "Approve with Caution" if score <= 60 else "Approve"

def suggest_limit_and_rate(avg_monthly_credits, score):
    limit = 8 * avg_monthly_credits if avg_monthly_credits else None
    rate = 9.0 if score > 70 else 11.0 if score >= 40 else 13.0
    return limit, rate

def format_inr(x):
    return f"₹{x:,.0f}" if x else "N/A"

# ---------- UI ----------
st.title("🧠 Intelli‑Credit Prototype")
st.caption("A smart, rule‑based Credit Appraisal Memo (CAM) generator for demo purposes.")

# Sidebar
st.sidebar.header("🏢 Borrower Info")
company_name = st.sidebar.text_input("Company name", "ABC Pvt Ltd")
sector = st.sidebar.text_input("Sector", "Manufacturing")
user_legal_disputes = st.sidebar.checkbox("Known legal disputes?")
user_adverse_news = st.sidebar.checkbox("Adverse news?")
user_reg_action = st.sidebar.checkbox("Regulatory action?")
st.sidebar.markdown("---")
st.sidebar.caption("Prototype for hackathon demo – not for real credit decisions.")

# Tabs
tab1, tab2, tab3 = st.tabs(["📂 Upload & Extract", "⚙️ Risk & Scoring", "📄 CAM Generator"])

# ---------- Tab 1 ----------
with tab1:
    st.subheader("Step 1: Upload Documents")
    col_pdf, col_gst, col_bank = st.columns(3)
    with col_pdf:
        annual_report_file = st.file_uploader("Annual Report (PDF)", type=["pdf"])
    with col_gst:
        gst_file = st.file_uploader("GST Summary (CSV)", type=["csv"])
    with col_bank:
        bank_file = st.file_uploader("Bank Statement (CSV)", type=["csv"])

    pdf_text = ""
    if annual_report_file:
        with st.spinner("Extracting text from PDF..."):
            pdf_text = extract_text_from_pdf(annual_report_file)
        st.text_area("Extracted Text (first 1000 chars)", pdf_text[:1000], height=200)

    gst_df, bank_df = None, None
    if gst_file:
        try:
            gst_df = pd.read_csv(gst_file)
            st.success("✅ GST Summary Loaded")
            st.dataframe(gst_df.head())
        except Exception as e:
            st.error(f"Error reading GST CSV: {e}")
    if bank_file:
        try:
            bank_df = pd.read_csv(bank_file)
            st.success("✅ Bank Statement Loaded")
            st.dataframe(bank_df.head())
        except Exception as e:
            st.error(f"Error reading Bank CSV: {e}")

# ---------- Tab 2 ----------
with tab2:
    st.subheader("Step 2: Risk & Scoring")
    col_f1, col_f2, col_f3 = st.columns(3)
    revenue = find_number_after_keyword(pdf_text, ["revenue from operations", "total revenue"])
    profit = find_number_after_keyword(pdf_text, ["profit after tax", "profit for the year"])
    total_debt = find_number_after_keyword(pdf_text, ["total borrowings", "total debt"])
    col_f1.metric("Approx. Revenue", format_inr(revenue))
    col_f2.metric("Approx. Net Profit", format_inr(profit))
    col_f3.metric("Approx. Total Debt", format_inr(total_debt))

    st.markdown("### 📊 Financial Ratios")
    col_a1, col_a2 = st.columns(2)
    equity_input = col_a1.number_input("Shareholders’ Equity (₹)", min_value=0.0, step=1e5)
    prev_rev_input = col_a2.number_input("Previous Year Revenue (₹)", min_value=0.0, step=1e5)

    d_to_e = total_debt / equity_input if equity_input > 0 and total_debt else None
    rev_growth = (revenue - prev_rev_input) / prev_rev_input * 100 if revenue and prev_rev_input > 0 else None

    col_r1, col_r2 = st.columns(2)
    col_r1.write(f"**Debt‑to‑Equity:** {d_to_e:.2f}" if d_to_e else "**Debt‑to‑Equity:** N/A")
    col_r2.write(f"**Revenue Growth:** {rev_growth:.1f}%" if rev_growth else "**Revenue Growth:** N/A")

    st.markdown("### 🔄 GST vs Bank Behaviour")
    merged_df, circular_series, circular_flag = compute_gst_bank_flags(gst_df, bank_df)
    if merged_df is not None:
        st.dataframe(merged_df)
        if circular_flag:
            st.error("⚠️ Potential circular trading detected (>30% mismatch).")
        else:
            st.success("✅ No major mismatch detected.")
    else:
        st.info("Upload valid GST and Bank CSVs to enable this check.")

    st.markdown("### ⚖️ Litigation / Adverse Info")
    litigation_flag, hits = detect_litigation_flag(pdf_text) if pdf_text else (False, 0)
    combined_litigation_flag = litigation_flag or user_legal_disputes or user_adverse_news or user_reg_action
    st.write(f"Keyword hits: **{hits}** | Flag: **{'High' if combined_litigation_flag else 'Low'}**")

    features = {
        "d_to_e": d_to_e,
        "rev_growth": rev_growth,
        "circular_flag": bool(circular_flag),
        "litigation_flag": bool(combined_litigation_flag),
    }
    score = compute_score(features)
    decision = map_decision(score)
    avg_monthly_credits = bank_df["credits_from_sales"].mean() if bank_df is not None and "credits_from_sales" in bank_df.columns else None
    limit, rate = suggest_limit_and_rate(avg_monthly_credits, score)

    st.markdown("### 🧮 Overall Risk Summary")
    col_s1, col_s2, col_s3 = st.columns(3)
    col_s1.metric("Risk Score", f"{score}/100")
    col_s2.metric("Decision", decision)
    col_s3.metric("Suggested Rate", f"{rate:.1f}%")
    st.write(f"**Suggested Limit:** {format_inr(limit)}")

# ---------- Tab 3 ----------
with tab3:
    st.subheader("Step 3: Credit Appraisal Memo (CAM)")
    st.info("Auto‑generated summary based on extracted data and rule‑based scoring.")
    st.markdown("---")
    st.write("📄 The CAM text and download options will appear here (same logic as your original code).")
