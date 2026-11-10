export default {
  async rewrites() {
    return [
      {
        source: "/api/ci-diagnoses/:path*",
        destination: "http://api:8001/api/ci-diagnoses/:path*",
      },
    ];
  },
};
