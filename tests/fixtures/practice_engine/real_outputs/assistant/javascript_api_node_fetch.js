const fetch = require('node-fetch');

async function test() {
  const response = await fetch(process.env.PRACTICE_API_URL + 'login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      email: 'testuser@library.test',
      password: 'Test@123'
    })
  });

  const status = response.status;
  console.log(`Status check: ${status === 200 ? 'PASS' : 'FAIL'} (expected 200, got ${status})`);

  const data = await response.json();
  const name = data.name;
  console.log(`Name check: ${name === 'Test User' ? 'PASS' : 'FAIL'} (expected 'Test User', got '${name}')`);
}

test().catch(err => {
  console.error('Test failed with error:', err);
  process.exit(1);
});