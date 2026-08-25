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

function groupBy(items, keyFn) {
  const groups = {};
  for (const item of items) {
    const key = keyFn(item);
    if (!groups[key]) {
      groups[key] = [];
    }
    groups[key].push(item);
  }
  return groups;
}

function isEmpty(arr) {
  return arr.length === 0;
}
