// SortableJS wiring only: priority order, Today reorder, and late-open Done / Not done lists.
function setup(root) {
  const priorities = root.querySelector("#priority-list");
  if (priorities) {
    new Sortable(priorities, {
      animation: 120,
      onEnd() {
        const order = [...priorities.children].map((el) => el.dataset.key).join(",");
        htmx.ajax("POST", "/profile/priorities", { values: { order }, target: "body" });
      },
    });
  }

  const today = root.querySelector("#today-list");
  if (today) {
    new Sortable(today, {
      animation: 120,
      handle: ".drag",
      onEnd(event) {
        const previous = event.item.previousElementSibling;
        if (!previous) return;
        htmx.ajax("POST", "/items/reorder", {
          values: { item_key: event.item.dataset.key, after: previous.dataset.key },
          target: "body",
        });
      },
    });
  }

  for (const list of root.querySelectorAll(".late-list")) {
    new Sortable(list, { group: "late", animation: 120 });
  }
  const lateForm = root.querySelector("#late-form");
  if (lateForm) {
    lateForm.addEventListener("submit", () => {
      for (const name of ["done", "not_done"]) {
        const keys = [...root.querySelectorAll(`#late-${name} [data-key]`)].map((el) => el.dataset.key);
        lateForm.querySelector(`input[name=${name}]`).value = keys.join(",");
      }
    });
  }
}

htmx.onLoad(setup);
