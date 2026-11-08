import os
import time

import requests # type: ignore
import streamlit as st # type: ignore

API_URL = "https://api.assemblyai.com"
GATEWAY_URL = "https://llm-gateway.assemblyai.com/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are a medical scribe. Create a draft SOAP note from the encounter "
    "transcript below. "
    "Speakers are labeled A and B and their roles are unknown: do not "
    "assume which one is the clinician; infer roles only if the transcript "
    "states them, and flag it if unclear. "
    "Use only explicitly stated facts; never infer diagnoses, "
    "medications, measurements, or plans. Write 'Not documented' "
    "when absent. Preserve uncertainty and speaker attribution. "
    "Return sections: Subjective, Objective, Assessment, Plan, "
    "and Review Flags. This requires clinician review."
)


def transcribe(audio_bytes, key, terms):
    """Audio bytes -> 'Speaker A: ...' text, using AssemblyAI's web API directly."""
    headers = {"authorization": key}

    # 1. Upload the audio file.
    upload = requests.post(
        f"{API_URL}/v2/upload", headers=headers, data=audio_bytes, timeout=300
    )
    upload.raise_for_status()
    audio_url = upload.json()["upload_url"]

    # 2. Start the transcription (Medical Mode + speaker labels).
    body = {
        "audio_url": audio_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "domain": "medical-v1",
        "speaker_labels": True,
    }
    if terms:
        body["keyterms_prompt"] = terms
    job = requests.post(
        f"{API_URL}/v2/transcript", headers=headers, json=body, timeout=60
    )
    job.raise_for_status()
    transcript_id = job.json()["id"]

    # 3. Check every few seconds until it's done.
    deadline = time.time() + 600
    while time.time() < deadline:
        result = requests.get(
            f"{API_URL}/v2/transcript/{transcript_id}", headers=headers, timeout=60
        ).json()
        if result["status"] == "completed":
            utterances = result.get("utterances") or []
            lines = [f"Speaker {u['speaker']}: {u['text']}" for u in utterances]
            return "\n".join(lines) or (result.get("text") or "")
        if result["status"] == "error":
            raise RuntimeError(result.get("error", "Unknown transcription error"))
        time.sleep(3)
    raise RuntimeError("Timed out waiting for the transcript.")


def build_prompt(dialogue):
    return SYSTEM_PROMPT + "\n\nTranscript:\n" + dialogue


def draft_note(dialogue, key):
    """Transcript text -> draft SOAP note (AssemblyAI LLM Gateway)."""
    response = requests.post(
        GATEWAY_URL,
        headers={"authorization": key, "Content-Type": "application/json"},
        json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": build_prompt(dialogue)}],
            "max_tokens": 2000,
        },
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(f"{response.status_code}: {response.text[:300]}")
    return response.json()["choices"][0]["message"]["content"]


st.set_page_config(page_title="Practice Medical Scribe", page_icon="🩺", layout="wide")
st.title("🩺 Practice AI Medical Scribe")
st.warning(
    "Learning project. Use fake, made-up conversations only. "
    "The note is an AI-generated draft and must be reviewed by a human."
)

api_key = os.environ.get("ASSEMBLYAI_API_KEY") or st.text_input(
    "AssemblyAI API key",
    type="password",
    help="Not saved anywhere. It only lasts while this page is open.",
)

uploaded = st.file_uploader(
    "Upload a practice recording", type=["mp3", "wav", "m4a"]
)
terms_text = st.text_input(
    "Medication names or terms the transcriber might miss (optional, separated by commas)",
    value="metoprolol, lisinopril, hemoglobin A1c",
)

if st.button("Create note", type="primary", disabled=not (uploaded and api_key)):
    terms = [t.strip() for t in terms_text.split(",") if t.strip()]
    st.session_state.pop("transcript", None)
    st.session_state.pop("note", None)

    try:
        with st.spinner("Transcribing (this can take a minute)..."):
            st.session_state["transcript"] = transcribe(
                uploaded.getvalue(), api_key, terms
            )
    except Exception as error:
        st.error(f"Transcription failed: {error}")

    if st.session_state.get("transcript"):
        try:
            with st.spinner("Writing the SOAP note..."):
                st.session_state["note"] = draft_note(
                    st.session_state["transcript"], api_key
                )
        except Exception as error:
            st.error(
                f"Note generation failed: {error}. "
                "Your transcript is below. Open the backup prompt next to it "
                "to get a note from any chatbot instead."
            )
    elif "transcript" in st.session_state:
        st.error("The transcript came back empty. Check that the audio has speech.")

if st.session_state.get("transcript"):
    left, right = st.columns(2)

    with left:
        st.subheader("Transcript")
        st.text(st.session_state["transcript"])
        st.download_button(
            "Download transcript",
            st.session_state["transcript"],
            file_name="transcript.txt",
        )

    with right:
        st.subheader("Draft SOAP note")
        if st.session_state.get("note"):
            st.markdown(st.session_state["note"])
            st.download_button(
                "Download note",
                st.session_state["note"],
                file_name="soap_note.txt",
            )
        else:
            st.info("No note yet.")
            with st.expander("Backup: copy this prompt into Claude or another chatbot"):
                st.code(build_prompt(st.session_state["transcript"]), language=None)