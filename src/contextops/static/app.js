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
  window.alert(error.message);
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  button.disabled = true;
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
  if (request) request.then(() => window.location.reload()).catch(showError);
});

const decisionForm = document.querySelector("[data-decision-form]");
if (decisionForm) {
  decisionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(decisionForm);
    try {
      await postJson(`/api/cases/${decisionForm.dataset.caseId}/decision`, {
        decision: form.get("decision"),
        rationale: form.get("rationale"),
      });
      window.location.reload();
    } catch (error) {
      showError(error);
    }
  });
}
