//! Exercise the production Block and native source without loading a renderer,
//! ASIO DLL, network device, Python callback or foreign pool implementation.
#[path = "../src/output/output_pool.rs"]
mod output_pool;
#[path = "../src/dsp/output_processing.rs"]
mod output_processing;
#[path = "../src/dsp/limiter_control.rs"]
mod limiter_control;
