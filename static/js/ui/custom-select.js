// Searchable dropdown that replaces a native <select> (styles: static/css/controls.css).
// The native select stays in the page, keeps its value and still fires "change";
// options added to it later (or a change of `disabled`) re-render the menu.

export function enhanceSelect(select) {
  const label = document.querySelector(`label[for="${select.id}"]`);
  const wrapper = document.createElement("div");
  wrapper.className = "custom-select";
  select.before(wrapper);
  wrapper.appendChild(select);
  select.classList.add("custom-select-source");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");

  const trigger = document.createElement("button");
  trigger.id = `${select.id}-button`;
  trigger.type = "button";
  trigger.className = "custom-select-trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-controls", `${select.id}-listbox`);
  if (label) {
    trigger.setAttribute("aria-label", label.textContent.trim());
    label.htmlFor = trigger.id;
  }
  const describedBy = select.getAttribute("aria-describedby");
  if (describedBy) trigger.setAttribute("aria-describedby", describedBy);
  const triggerText = document.createElement("span");
  triggerText.className = "custom-select-value";
  trigger.appendChild(triggerText);

  // Long lists get a search box at the top of the menu.
  const searchable = select.options.length > 8 || select.hasAttribute("data-searchable");
  const popover = document.createElement("div");
  popover.className = "custom-select-popover";
  popover.hidden = true;
  let search = null;
  let countLabel = null;
  if (searchable) {
    const searchRow = document.createElement("div");
    searchRow.className = "custom-select-search";
    search = document.createElement("input");
    search.type = "search";
    search.placeholder = "Type to search...";
    search.setAttribute("aria-label", `Search ${label ? label.textContent.trim().toLocaleLowerCase() : "options"}`);
    search.setAttribute("aria-controls", `${select.id}-listbox`);
    search.autocomplete = "off";
    countLabel = document.createElement("span");
    countLabel.className = "custom-select-count";
    countLabel.setAttribute("aria-live", "polite");
    searchRow.append(search, countLabel);
    popover.appendChild(searchRow);
  }
  const menu = document.createElement("div");
  menu.id = `${select.id}-listbox`;
  menu.className = "custom-select-menu";
  menu.setAttribute("role", "listbox");
  menu.setAttribute("aria-labelledby", trigger.id);
  const empty = document.createElement("p");
  empty.className = "custom-select-empty";
  empty.textContent = "No matches. Try another spelling.";
  empty.hidden = true;
  popover.append(menu, empty);
  wrapper.append(trigger, popover);

  let typeahead = "";
  let typeaheadTimer = null;
  const options = () => [...menu.querySelectorAll(".custom-select-option:not([hidden])")];
  const sync = () => {
    const selected = select.options[select.selectedIndex];
    triggerText.replaceChildren();
    const main = document.createElement("span");
    main.textContent = selected?.dataset.triggerLabel || selected?.textContent.trim() || "Select an option";
    triggerText.appendChild(main);
    if (selected?.value && selected.dataset.meta) {
      const meta = document.createElement("small");
      meta.textContent = selected.dataset.meta;
      triggerText.appendChild(meta);
    }
    trigger.classList.toggle("is-placeholder", !selected?.value);
    trigger.disabled = select.disabled;
    menu.querySelectorAll(".custom-select-option").forEach((option) => {
      option.setAttribute("aria-selected", String(option.dataset.value === select.value));
    });
  };
  const close = (returnFocus = false) => {
    popover.hidden = true;
    wrapper.classList.remove("is-open");
    trigger.setAttribute("aria-expanded", "false");
    if (returnFocus) trigger.focus();
  };
  const choose = (option) => {
    if (!option || option.getAttribute("aria-disabled") === "true" || select.disabled) return;
    select.value = option.dataset.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    sync();
    close(true);
  };
  const addOption = (nativeOption) => {
    if (nativeOption.hasAttribute("data-placeholder")) return; // prompt text only, not a choice
    const option = document.createElement("div");
    option.className = "custom-select-option";
    option.setAttribute("role", "option");
    option.tabIndex = -1;
    option.dataset.value = nativeOption.value;
    const name = document.createElement("span");
    name.className = "custom-select-option-name";
    name.textContent = nativeOption.textContent.trim();
    option.appendChild(name);
    if (nativeOption.dataset.meta) {
      const meta = document.createElement("span");
      meta.className = "custom-select-option-meta";
      meta.textContent = nativeOption.dataset.meta;
      option.appendChild(meta);
    }
    option.dataset.search = `${nativeOption.textContent} ${nativeOption.dataset.meta || ""}`.toLocaleLowerCase();
    if (nativeOption.disabled) option.setAttribute("aria-disabled", "true");
    menu.appendChild(option);
  };
  const renderOptions = () => {
    menu.replaceChildren();
    [...select.children].forEach((child) => {
      if (child instanceof HTMLOptGroupElement) {
        const groupLabel = document.createElement("div");
        groupLabel.className = "custom-select-group";
        groupLabel.textContent = child.label;
        menu.appendChild(groupLabel);
        Array.from(child.children).forEach(addOption);
      } else if (child instanceof HTMLOptionElement) {
        addOption(child);
      }
    });
    sync();
  };
  const applyFilter = () => {
    if (!search) return;
    const terms = search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    let visible = 0;
    let group = null;
    let groupHasVisible = false;
    const finishGroup = () => { if (group) group.hidden = !groupHasVisible; };
    [...menu.children].forEach((node) => {
      if (node.classList.contains("custom-select-group")) {
        finishGroup();
        group = node;
        groupHasVisible = false;
        return;
      }
      const match = terms.every((term) => node.dataset.search.includes(term));
      node.hidden = !match;
      if (match) { visible += 1; groupHasVisible = true; }
    });
    finishGroup();
    empty.hidden = visible > 0;
    countLabel.textContent = terms.length ? `${visible} found` : `${visible} total`;
  };
  const open = (focusTarget) => {
    if (select.disabled) return;
    renderOptions();
    if (search) {
      search.value = "";
      applyFilter();
    }
    popover.hidden = false;
    wrapper.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
    const selected = options().find((option) => option.dataset.value === select.value);
    selected?.scrollIntoView({ block: "nearest" });
    if (focusTarget === "search" && search) search.focus();
    else if (focusTarget === "selected") (selected || options()[0])?.focus();
  };
  const focusTypeaheadMatch = (key) => {
    const character = key.toLocaleLowerCase();
    typeahead = typeahead && Array.from(typeahead).every((item) => item === character)
      ? character
      : typeahead + character;
    clearTimeout(typeaheadTimer);
    typeaheadTimer = setTimeout(() => { typeahead = ""; }, 700);
    const match = options().find((option) =>
      option.textContent.trim().toLocaleLowerCase().startsWith(typeahead)
    );
    if (match) match.focus();
  };
  const isPrintable = (event) =>
    event.key.length === 1 && event.key !== " " && !event.altKey && !event.ctrlKey && !event.metaKey;

  trigger.addEventListener("click", () => {
    if (popover.hidden) open(search ? "search" : null);
    else close();
  });
  trigger.addEventListener("keydown", (event) => {
    if (isPrintable(event)) {
      event.preventDefault();
      if (search) {
        open("search");
        search.value = event.key;
        applyFilter();
      } else {
        open(null);
        focusTypeaheadMatch(event.key);
      }
      return;
    }
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      open(search ? "search" : "selected");
    } else if (event.key === "Escape") {
      close();
    }
  });
  if (search) {
    search.addEventListener("input", applyFilter);
    search.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        options()[0]?.focus();
      } else if (event.key === "Enter") {
        event.preventDefault();
        choose(options()[0]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        close(true);
      }
    });
  }
  menu.addEventListener("click", (event) => {
    const option = event.target.closest(".custom-select-option");
    if (option) choose(option);
  });
  menu.addEventListener("keydown", (event) => {
    const list = options();
    const index = list.indexOf(document.activeElement);
    if (isPrintable(event)) {
      event.preventDefault();
      if (search) {
        search.focus();
        search.value += event.key;
        applyFilter();
      } else {
        focusTypeaheadMatch(event.key);
      }
    } else if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (event.key === "ArrowUp" && index <= 0 && search) {
        search.focus();
        return;
      }
      const direction = event.key === "ArrowDown" ? 1 : -1;
      list[(index + direction + list.length) % list.length]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      list[event.key === "Home" ? 0 : list.length - 1]?.focus();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(document.activeElement);
    } else if (event.key === "Tab") {
      close();
    }
  });
  document.addEventListener("click", (event) => {
    if (!wrapper.contains(event.target)) close();
  });
  select.addEventListener("change", sync);
  new MutationObserver(() => {
    renderOptions();
    sync();
  }).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "label"] });
  renderOptions();
}
