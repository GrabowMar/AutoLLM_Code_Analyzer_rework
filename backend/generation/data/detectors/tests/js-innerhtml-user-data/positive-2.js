function greet(el, name) {
  el.innerHTML = `<p>Hello ${name}</p>`;
  el.insertAdjacentHTML("beforeend", name);
}
