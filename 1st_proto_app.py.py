import io
import re
import numpy as np
import pandas as pd
import streamlit as st

# --------- Optional PyMuPDF (fitz) for local PDF parsing ---------
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except Exception:
    HAS_FITZ = False

# --------- Optional FPDF for CAM PDF (Cloud pe fail ho sakta) ---------
try:
    from fpdf import FPDF  # for CAM PDF download
    HAS_FPDF = True
except Exception:
    HAS_FPDF = False



# ---------- Helper functions ----------

def extract_text_from_pdf(uploaded_file):
    if uploaded_file is None:
        return ""
    data = uploaded_file.read()
    if HAS_FITZ:
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            text = ""
            for page in doc:
                text += page.get_text()
            return text
        except Exception:
            # if something still fails, fall back gracefully
            return "Error while parsing PDF. Please check the file or try locally."
    # Fallback for cloud / no fitz
    return "PDF text extraction is not available on this deployment. Run locally with PyMuPDF for full functionality."


def find_number_after_keyword(text, keywords):
    """
    Very rough heuristic: search for keyword, then pick the first large number on that line.
    """
    text_lower = text.lower()
    lines = text_lower.splitlines()
    for line in lines:
        for kw in keywords:
            if kw.lower() in line:
                nums = re.findall(r"[0-9,]+\.?[0-9]*", line)
                if nums:
                    vals = []
                    for n in nums:
                        try:
                            vals.append(float(n.replace(",", "")))
                        except:
                            pass
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
    has_circular_flag = circular_flag.any()

    return merged, circular_flag, has_circular_flag


def detect_litigation_flag(text):
    keywords = ["litigation", "dispute", "penalty", "show cause", "nclt", "tribunal", "suit"]
    text_low = text.lower()
    hits = sum(text_low.count(k) for k in keywords)
    return hits > 3, hits


def compute_score(features):
    score = 50

    d_to_e = features.get("d_to_e")
    if d_to_e is not None:
        if d_to_e < 1:
            score += 10
        elif d_to_e <= 2:
            score += 5
        else:
            score -= 10

    rev_growth = features.get("rev_growth")
    if rev_growth is not None:
        if rev_growth > 10:
            score += 10
        elif rev_growth >= 0:
            score += 5
        else:
            score -= 10

    if features.get("circular_flag"):
        score -= 15

    if features.get("litigation_flag"):
        score -= 15

    score = max(0, min(100, score))
    return score


def map_decision(score):
    if score < 40:
        return "Reject"
    elif score <= 60:
        return "Approve with Caution"
    else:
        return "Approve"


def suggest_limit_and_rate(avg_monthly_credits, score):
    if avg_monthly_credits is None:
        limit = None
    else:
        limit = 8 * avg_monthly_credits  # simple heuristic

    if score > 70:
        rate = 9.0
    elif score >= 40:
        rate = 11.0
    else:
        rate = 13.0
    return limit, rate


def format_inr(x):
    if x is None:
        return "N/A"
    try:
        return f"₹{x:,.0f}"
    except:
        return str(x)


def cam_to_pdf_bytes(cam_text: str) -> bytes:
    """Convert CAM text to a simple PDF using FPDF."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Arial", size=12)

    for line in cam_text.split("\n"):
        pdf.multi_cell(0, 8, txt=line)
    pdf_bytes = pdf.output(dest="S").encode("latin1")
    return pdf_bytes


# ---------- Streamlit UI ----------

st.set_page_config(page_title="Intelli-Credit Prototype", layout="wide")
st.title("🧠 Intelli‑Credit Prototype (CAM Generator)")

st.write(
    "Upload basic financial documents, run rule‑based checks, and generate a simple Credit Appraisal Memo (CAM)."
)

# Sidebar: company info
st.sidebar.header("Borrower Info")
company_name = st.sidebar.text_input("Company name", "ABC Pvt Ltd")
sector = st.sidebar.text_input("Sector", "Manufacturing")
user_legal_disputes = st.sidebar.checkbox("User knows major legal disputes?")
user_adverse_news = st.sidebar.checkbox("User knows adverse news?")
user_reg_action = st.sidebar.checkbox("User knows regulatory action?")

st.sidebar.markdown("---")
st.sidebar.caption("Prototype for hackathon demo – not for real credit decisions.")

# Tabs for flow
tab1, tab2, tab3 = st.tabs(["1. Upload & Extract", "2. Risk & Scoring", "3. CAM Generator"])

# ---------- Tab 1: Upload & Extract ----------
with tab1:
    st.subheader("Step 1: Upload documents")

    col_pdf, col_gst, col_bank = st.columns(3)

    with col_pdf:
        annual_report_file = st.file_uploader(
            "Annual report (PDF)",
            type=["pdf"],
            key="annual_report"
        )
    with col_gst:
        gst_file = st.file_uploader(
            "GST summary (CSV)",
            type=["csv"],
            key="gst"
        )
    with col_bank:
        bank_file = st.file_uploader(
            "Bank statement (CSV)",
            type=["csv"],
            key="bank"
        )

    # Extract PDF text
    pdf_text = ""
    if annual_report_file is not None:
        st.markdown("**Extracting text from annual report...**")
        pdf_text = extract_text_from_pdf(annual_report_file)
        st.text_area("Sample extracted text (first 1000 chars)", pdf_text[:1000], height=200)

    # Show structured files
    gst_df, bank_df = None, None

    if gst_file is not None:
        try:
            gst_df = pd.read_csv(gst_file)
            st.markdown("**GST summary preview**")
            st.dataframe(gst_df.head())
        except Exception as e:
            st.error(f"Error reading GST CSV: {e}")

    if bank_file is not None:
        try:
            bank_df = pd.read_csv(bank_file)
            st.markdown("**Bank statement preview**")
            st.dataframe(bank_df.head())
        except Exception as e:
            st.error(f"Error reading Bank CSV: {e}")

    st.info("Move to the next tab after uploading files to view risk checks and scoring.")

# ---------- Tab 2: Risk & Scoring ----------
with tab2:
    st.subheader("Step 2: Rule‑based risk checks")

    # Basic extraction from PDF text (very rough)
    col_f1, col_f2, col_f3 = st.columns(3)

    with col_f1:
        revenue = find_number_after_keyword(pdf_text, ["revenue from operations", "total revenue"])
        st.metric("Approx. Revenue", format_inr(revenue))
    with col_f2:
        profit = find_number_after_keyword(pdf_text, ["profit after tax", "profit for the year"])
        st.metric("Approx. Net Profit", format_inr(profit))
    with col_f3:
        total_debt = find_number_after_keyword(pdf_text, ["total borrowings", "total debt"])
        st.metric("Approx. Total Debt", format_inr(total_debt))

    # User input for equity and previous year revenue so we can compute ratios
    st.markdown("### Additional inputs (for ratios)")
    col_a1, col_a2 = st.columns(2)
    with col_a1:
        equity_input = st.number_input("Shareholders' equity (₹)", min_value=0.0, step=1e5, format="%.0f")
    with col_a2:
        prev_rev_input = st.number_input("Previous year revenue (₹)", min_value=0.0, step=1e5, format="%.0f")

    d_to_e = None
    rev_growth = None

    if equity_input > 0 and total_debt:
        d_to_e = total_debt / equity_input

    if revenue and prev_rev_input > 0:
        rev_growth = (revenue - prev_rev_input) / prev_rev_input * 100

    col_r1, col_r2 = st.columns(2)
    with col_r1:
        st.write(f"**Debt‑to‑equity**: {d_to_e:.2f}" if d_to_e is not None else "**Debt‑to‑equity**: N/A")
    with col_r2:
        st.write(f"**Revenue growth**: {rev_growth:.1f}%"
                 if rev_growth is not None else "**Revenue growth**: N/A")

    st.markdown("### GST vs Bank behaviour")

    merged_df, circular_series, circular_flag = compute_gst_bank_flags(gst_df, bank_df)
    if merged_df is not None:
        st.dataframe(merged_df)
        if circular_flag:
            st.error("⚠️ Potential circular trading / revenue inflation flagged (GST vs bank mismatch > 30%).")
        else:
            st.success("✅ No major GST vs bank mismatch detected (within 30%).")
    else:
        st.info("Upload GST and Bank CSVs with columns: `month, gst_sales` and `month, credits_from_sales` to enable this check.")

    st.markdown("### Litigation / adverse information")

    litigation_flag, hits = detect_litigation_flag(pdf_text) if pdf_text else (False, 0)
    combined_litigation_flag = litigation_flag or user_legal_disputes or user_adverse_news or user_reg_action

    col_l1, col_l2 = st.columns(2)
    with col_l1:
        st.write(f"Keyword hits in documents: **{hits}**")
    with col_l2:
        st.write(f"Litigation / adverse flag (combined): **{'High' if combined_litigation_flag else 'Low'}**")

    # Final score
    st.markdown("### Overall risk score")

    features = {
        "d_to_e": d_to_e,
        "rev_growth": rev_growth,
        "circular_flag": bool(circular_flag),
        "litigation_flag": bool(combined_litigation_flag),
    }

    score = compute_score(features)
    decision = map_decision(score)

    avg_monthly_credits = None
    if bank_df is not None and "credits_from_sales" in bank_df.columns:
        avg_monthly_credits = bank_df["credits_from_sales"].mean()

    limit, rate = suggest_limit_and_rate(avg_monthly_credits, score)

    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        st.metric("Risk score", f"{score}/100")
    with col_s2:
        st.metric("Decision", decision)
    with col_s3:
        st.metric("Suggested rate", f"{rate:.1f}%")

    st.write(f"**Suggested limit**: {format_inr(limit)}")

    st.info("Go to the CAM Generator tab to view the auto‑generated memo.")

# ---------- Tab 3: CAM Generator ----------
with tab3:
    st.subheader("Step 3: Credit Appraisal Memo (CAM)")

    # Interpret features into text
    if d_to_e is None:
        capacity_desc = "capital structure information is limited in this prototype."
    elif d_to_e < 1:
        capacity_desc = f"Debt‑to‑equity of {d_to_e:.2f} indicates strong capacity to service additional debt."
    elif d_to_e <= 2:
        capacity_desc = f"Debt‑to‑equity of {d_to_e:.2f} indicates moderate capacity to service additional debt."
    else:
        capacity_desc = f"Debt‑to‑equity of {d_to_e:.2f} indicates stressed capacity to service additional debt."

    if rev_growth is None:
        growth_desc = "Revenue growth trend is not fully available."
    elif rev_growth > 10:
        growth_desc = f"Revenue growth of {rev_growth:.1f}% reflects healthy business momentum."
    elif rev_growth >= 0:
        growth_desc = f"Revenue growth of {rev_growth:.1f}% reflects stable but modest growth."
    else:
        growth_desc = f"Revenue de‑growth of {rev_growth:.1f}% signals pressure on top‑line."

    character_text = (
        f"The borrower operates in the {sector} sector and has "
        f"{'some' if combined_litigation_flag else 'no material'} indicators of litigation or regulatory disputes "
        "based on document scan and user inputs."
    )

    capacity_text = capacity_desc + " " + growth_desc

    capital_text = (
        f"Approximate revenue of {format_inr(revenue)} and net profit of {format_inr(profit)} "
        "suggest the internal accrual capacity for this prototype. "
        "Detailed balance sheet analysis is not modelled in this version."
    )

    collateral_text = (
        "Collateral details are not captured in this prototype; the recommendation is primarily driven by "
        "cash‑flow behaviour, GST‑bank reconciliation and basic financial ratios."
    )

    conditions_text = (
        f"The borrower operates in the {sector} sector. Macro and sectoral conditions are assumed "
        "to be broadly stable for this demonstration."
    )

    explanation_text = (
        f"The model assigned a score of {score}/100, driven by debt‑to‑equity, revenue growth, "
        f"GST vs bank consistency and litigation indicators. The decision category is '{decision}', "
        f"and a working capital limit of approximately {format_inr(limit)} at an indicative rate of {rate:.1f}% "
        "is suggested for demonstration purposes."
    )

    cam_md = f"""
### Credit Appraisal Memo (Prototype)

**Company**: {company_name}  
**Sector**: {sector}  
**Score**: {score}/100  
**Decision**: {decision}  

**Suggested Limit**: {format_inr(limit)}  
**Suggested Rate**: {rate:.1f}%  

#### Character
{character_text}

#### Capacity
{capacity_text}

#### Capital
{capital_text}

#### Collateral
{collateral_text}

#### Conditions
{conditions_text}

#### Rationale
{explanation_text}
"""

    st.markdown(cam_md)

    # ---------- CAM downloads: Markdown, CSV, PDF (if available) ----------

    # 1) Markdown
    cam_bytes_md = cam_md.encode("utf-8")
    st.download_button(
        label="Download CAM as Markdown",
        data=cam_bytes_md,
        file_name=f"CAM_{company_name.replace(' ', '_')}.md",
        mime="text/markdown",
    )

    # 2) CSV
    cam_dict = {
        "Company": [company_name],
        "Sector": [sector],
        "Score": [score],
        "Decision": [decision],
        "Suggested Limit": [format_inr(limit)],
        "Suggested Rate": [f"{rate:.1f}%"],
        "Character": [character_text],
        "Capacity": [capacity_text],
        "Capital": [capital_text],
        "Collateral": [collateral_text],
        "Conditions": [conditions_text],
        "Rationale": [explanation_text],
    }
    cam_df = pd.DataFrame(cam_dict)
    csv_bytes = cam_df.to_csv(index=False).encode("utf-8")

    st.download_button(
        label="Download CAM as CSV",
        data=csv_bytes,
        file_name=f"CAM_{company_name.replace(' ', '_')}.csv",
        mime="text/csv",
    )

    # 3) PDF (sirf tab jab FPDF available ho)
    if HAS_FPDF:
        cam_text_for_pdf = cam_md.replace("**", "")
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_font("Arial", size=12)
        for line in cam_text_for_pdf.split("\n"):
            pdf.multi_cell(0, 8, txt=line)
        pdf_bytes = pdf.output(dest="S").encode("latin1")

        st.download_button(
            label="Download CAM as PDF",
            data=pdf_bytes,
            file_name=f"CAM_{company_name.replace(' ', '_')}.pdf",
            mime="application/pdf",
        )
    else:
        st.info("PDF download not available on this deployment (FPDF not installed).")





