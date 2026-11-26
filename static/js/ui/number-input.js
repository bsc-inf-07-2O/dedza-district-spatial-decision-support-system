// Number input with ▲ / ▼ stepper buttons inside the field.

export function enhanceNumberInput(input) {
  const label = document.querySelector(`label[for="${input.id}"]`);
  const wrapper = document.createElement("div");
  wrapper.className = "number-input-control";
  input.before(wrapper);
  wrapper.appendChild(input);

  const stepper = document.createElement("div");
  stepper.className = "number-stepper";
  const createStepButton = (name, icon, direction) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "number-stepper-button";
    button.setAttribute("aria-label", `${name} ${label?.textContent.trim() || input.id}`);
    button.setAttribute("aria-controls", input.id);
    button.textContent = icon;
    button.addEventListener("click", () => {
      const previousValue = input.value;
      if (direction > 0) input.stepUp();
      else input.stepDown();
      if (input.value !== previousValue) {
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
      input.focus();
      updateButtons();
    });
    return button;
  };
  const increase = createStepButton("Increase", "▲", 1);
  const decrease = createStepButton("Decrease", "▼", -1);
  stepper.append(increase, decrease);
  wrapper.appendChild(stepper);

  const updateButtons = () => {
    const value = input.valueAsNumber;
    const min = input.min === "" ? -Infinity : Number(input.min);
    const max = input.max === "" ? Infinity : Number(input.max);
    increase.disabled = input.disabled || (Number.isFinite(value) && value >= max);
    decrease.disabled = input.disabled || (Number.isFinite(value) && value <= min);
  };
  input.addEventListener("input", updateButtons);
  input.addEventListener("change", updateButtons);
  updateButtons();
}
