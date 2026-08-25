function getName(user) {
  return user.name;
}

function double(x) {
  return x * 2;
}

function debounce(fn, waitMs) {
  let timer = null;
  return function debounced(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), waitMs);
  };
}

function dispatchEvent(name, payload) {
  return { name, payload, ts: Date.now() };
}
