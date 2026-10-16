async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}

function showError(error) {
  const status = document.querySelector("#app-status");
  if (status) status.textContent = error.message;
  window.alert(error.message);
}

function showStatus(message) {
  const status = document.querySelector("#app-status");
  if (status) status.textContent = message;
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  button.disabled = true;
  const originalLabel = button.textContent.trim();
  button.textContent = "Updating…";
  showStatus("Updating…");
  let request;
  if (action === "draft-rule") {
    request = postJson(`/api/cases/${button.dataset.caseId}/rules`, {
      issue_id: button.dataset.issueId,
    });
  } else if (action === "activate-rule") {
    request = postJson(`/api/rules/${button.dataset.ruleId}/activate`);
  } else if (action === "disable-rule") {
    request = postJson(`/api/rules/${button.dataset.ruleId}/disable`);
  } else if (action === "evaluate-rules") {
    request = postJson(`/api/cases/${button.dataset.caseId}/evaluate-rules`);
  }
  if (request) {
    request.then(() => window.location.reload()).catch((error) => {
      button.disabled = false;
      button.textContent = originalLabel;
      showError(error);
    });
  }
});

const decisionForm = document.querySelector("[data-decision-form]");
if (decisionForm) {
  decisionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(decisionForm);
    try {
      const editedProposalText = String(form.get("edited_proposal") || "").trim();
      let editedProposal;
      if (editedProposalText) {
        try {
          editedProposal = JSON.parse(editedProposalText);
        } catch (_error) {
          throw new Error("Edited proposal must be valid JSON.");
        }
      }
      await postJson(`/api/cases/${decisionForm.dataset.caseId}/decision`, {
        decision: form.get("decision"),
        rationale: form.get("rationale"),
        edited_proposal: editedProposal,
      });
      window.location.reload();
    } catch (error) {
      showError(error);
    }
  });
}
