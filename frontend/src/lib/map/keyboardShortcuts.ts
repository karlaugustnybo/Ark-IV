/** Window shortcuts must leave native controls and handled events alone. */
export function ignoresWindowShortcut(event: KeyboardEvent): boolean {
  return (
    event.defaultPrevented ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    (event.target instanceof Element &&
      Boolean(
        event.target.closest(
          'input, textarea, select, button, a, [contenteditable]:not([contenteditable="false"]), [role="button"], [role="combobox"], [role="textbox"], [role="listbox"], [role="slider"], [role="spinbutton"]'
        )
      ))
  );
}
