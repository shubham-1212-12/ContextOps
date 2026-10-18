async function postJson(url, payload) {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  const headers = { "Content-Type": "application/json" };
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json().catch(() => ({}));
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
        button.disabled = false;
        button.textContent = originalLabel;
        const notice = button.parentElement.querySelector("[data-duplicate-notice]");
        if (notice) {
          const message = document.createElement("div");
          const title = document.createElement("strong");
          title.textContent = "Equivalent rule already exists.";
          const detail = document.createElement("p");
          detail.append("No new rule was created. Rule #", String(data.id), " is ");
          const status = document.createElement("strong");
          status.textContent = data.duplicate_status;
          detail.append(status, " and remains unchanged.");
          const link = document.createElement("a");
          link.className = "button button-secondary";
          link.href = `/rules?duplicate_rule_id=${encodeURIComponent(data.id)}&return_case_id=${encodeURIComponent(button.dataset.caseId)}#rule-${data.id}`;
          link.textContent = "View existing rule";
          message.append(title, detail);
          notice.replaceChildren(message, link);
          notice.hidden = false;
        }
        showStatus("Equivalent rule found; no new rule was created.");
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
  const formError = newCaseForm.querySelector("[data-form-error]");
  let invalidField = null;

  function getFieldLabel(field) {
    const label = Array.from(newCaseForm.querySelectorAll("label")).find(
      (candidate) => candidate.htmlFor === field.id,
    );
    return label
      ? label.textContent.replace(/\s*\*\s*Required\s*$/i, "").trim()
      : field.name || "This field";
  }

  function clearFormError() {
    if (invalidField) {
      invalidField.removeAttribute("aria-invalid");
      invalidField = null;
    }
    if (formError) {
      formError.hidden = true;
      formError.textContent = "";
    }
    const status = document.querySelector("#app-status");
    if (status) status.textContent = "";
  }

  function showFormError(message, field = null) {
    if (invalidField && invalidField !== field) {
      invalidField.removeAttribute("aria-invalid");
    }
    invalidField = field;
    if (field) field.setAttribute("aria-invalid", "true");
    if (formError) {
      formError.textContent = message;
      formError.hidden = false;
    }
    const status = document.querySelector("#app-status");
    if (status) status.textContent = message;
  }

  newCaseForm.addEventListener("invalid", (event) => {
    const field = event.target;
    if (!field || !field.validity || invalidField) return;
    const message = field.validity.valueMissing
      ? `${getFieldLabel(field)} is required.`
      : `${getFieldLabel(field)} has an invalid value.`;
    showFormError(message, field);
  }, true);

  function clearErrorWhenCorrected(event) {
    const field = event.target;
    if (
      formError && !formError.hidden &&
      ((!invalidField && field.validity && field.validity.valid) ||
        (field === invalidField && field.validity && field.validity.valid))
    ) {
      clearFormError();
    }
  }

  newCaseForm.addEventListener("input", clearErrorWhenCorrected);
  newCaseForm.addEventListener("change", clearErrorWhenCorrected);

  newCaseForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!newCaseForm.checkValidity()) return;
    clearFormError();
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
      clearFormError();
      window.location.assign(`/cases/${response.case_id}`);
    } catch (error) {
      submitButton.disabled = false;
      submitButton.textContent = originalLabel;
      showError(error);
      showFormError(error.message);
    }
  });
}
