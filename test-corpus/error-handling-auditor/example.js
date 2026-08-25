async function safeFetch(url) {
  try {
    return await fetch(url);
  } catch (err) {
    return null;
  }
}

async function loadUser(id) {
  const res = await fetch(`/api/users/${id}`);
  return res.json();
}

function getUserName(user) {
  return user.name;
}
