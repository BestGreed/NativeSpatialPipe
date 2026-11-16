use clap::Parser;
use log::{error, info};
use std::io::Write;
use std::{fs::File, mem::size_of};
use tokio::sync::oneshot;

use inferno_aoip::device_server::{DeviceServer, Sample, Settings};

const ABOUT: &str = "Inferno2pipe
Copyright (C) 2023-2025 Teodor Wozniak

This program is free software: you can redistribute it and/or modify
it under the terms of the:

GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version,
or
the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License or the GNU Affero General Public License
for more details.

You should have received a copy of the GNU General Public License
and the GNU Affero General Public License along with this program.
If not, see <http://www.gnu.org/licenses/>.
";

#[derive(Parser, Debug)]
#[command(author, version, about = ABOUT, long_about = None, arg_required_else_help = true)]
struct Args {
    #[arg(long, short)]
    channels_count: usize,
    #[arg(long, short)]
    output: String,
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let logenv = env_logger::Env::default().default_filter_or("debug");
    env_logger::init_from_env(logenv);

    let args = Args::parse();
    let ch_count = args.channels_count;
    let (stop_tx, stop_rx) = oneshot::channel();
    let mut stop_tx = Some(stop_tx);

    let mut settings = Settings::new("Inferno2pipe", "Inferno2pipe", None, &Default::default());
    settings.make_rx_channels(ch_count);
    settings.make_tx_channels(0);

    let mut output_file = File::create(args.output).unwrap();
    let mut buffer: Vec<u8> =
        vec![0; ch_count * (settings.self_info.sample_rate as usize) * size_of::<Sample>() / 10];
    let write_callback = move |samples_count, channels: &Vec<Vec<Sample>>| {
        let stride = channels.len() * size_of::<Sample>();
        let len = stride * samples_count;
        if len > buffer.len() {
            buffer.resize(len, 0);
            info!("enlarging write buffer to {len}");
        }
        for (chi, ch) in channels.iter().enumerate() {
            let mut bi = chi * size_of::<Sample>();
            for si in 0..samples_count {
                buffer[bi..bi + size_of::<Sample>()].copy_from_slice(&ch[si].to_ne_bytes());
                bi += stride;
            }
        }
        if let Err(e) = output_file.write_all(&buffer[..len]) {
            error!("error writing output: {e:?}");
            stop_tx.take().map(|tx| tx.send(()));
        }
    };

    let mut server = DeviceServer::start(settings).await;
    server.receive_with_callback(Box::new(write_callback)).await;

    tokio::select! {
        _ = stop_rx => {}
        _ = tokio::signal::ctrl_c() => {}
    }
    server.shutdown().await;
}
