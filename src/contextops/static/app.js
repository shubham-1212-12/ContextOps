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
    request.then((data) => {
      if (action === "draft-rule" && data.duplicate) {
        window.location.assign(`/rules?duplicate_rule_id=${encodeURIComponent(data.id)}#rule-${data.id}`);
        return;
      }
      window.location.reload();
    }).catch((error) => {
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

const newCaseForm = document.querySelector("[data-new-case-form]");
if (newCaseForm) {
  newCaseForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(newCaseForm);
    const submitButton = newCaseForm.querySelector("button[type=submit]");
    const originalLabel = submitButton.textContent.trim();
    try {
      let blockedTime;
      try {
        const blockedText = String(form.get("blocked_time") || "").trim();
        if (!blockedText) throw new Error("Blocked time is required; use [] when none is known.");
        blockedTime = JSON.parse(blockedText);
      } catch (_error) {
        throw new Error("Blocked time must be valid JSON.");
      }
      if (!Array.isArray(blockedTime)) {
        throw new Error("Blocked time must be a JSON array.");
      }
      const constraints = String(form.get("constraints") || "")
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      submitButton.disabled = true;
      submitButton.textContent = "Creating…";
      showStatus("Creating case…");
      const response = await postJson("/api/cases", {
        title: form.get("title"),
        original_proposal: {
          event: form.get("event"),
          start: form.get("start"),
          duration_minutes: Number(form.get("duration_minutes")),
          timezone: form.get("proposal_timezone"),
        },
        context: {
          timezone: form.get("context_timezone"),
          working_hours: {
            start: form.get("working_hours_start"),
            end: form.get("working_hours_end"),
          },
          calendar_data: { status: form.get("calendar_status") },
          constraints,
          blocked_time: blockedTime,
        },
      });
      window.location.assign(`/cases/${response.case_id}`);
    } catch (error) {
      submitButton.disabled = false;
      submitButton.textContent = originalLabel;
      showError(error);
    }
  });
}
