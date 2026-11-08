function reserve(open) {
  if (open) {
    return "reserved";
  }
  return "unavailable";
}

console.log(reserve(process.argv[2] === "open"));
