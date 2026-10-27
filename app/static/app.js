// SortableJS wiring only: priority order, Today reorder, and late-open Done / Not done lists.
function syncLateForm(root) {
  const form = root.querySelector("#late-form");
  if (!form) return;
  for (const name of ["done", "not_done"]) {
    const keys = [...form.querySelectorAll(`#late-${name} [data-key]`)].map((el) => el.dataset.key);
    form.querySelector(`input[name=${name}]`).value = keys.join(",");
  }
}

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
        if (!previous || event.oldIndex === event.newIndex) return;
        htmx.ajax("POST", "/items/reorder", {
          values: { item_key: event.item.dataset.key, after: previous.dataset.key },
          target: "body",
        });
      },
    });
  }

  // The hidden fields stay current, because htmx reads a boosted form as soon as it submits.
  for (const list of root.querySelectorAll(".late-list")) {
    new Sortable(list, { group: "late", animation: 120, onSort: () => syncLateForm(root) });
  }
  syncLateForm(root);
}

htmx.onLoad(setup);
