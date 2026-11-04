function render(el, text) {
  el.textContent = text;
  if (el.innerHTML === "") return;
  el.innerHTML = "";
  el.innerHTML = '<b>static</b>';
  el.insertAdjacentHTML("beforeend", "<hr>");
}
