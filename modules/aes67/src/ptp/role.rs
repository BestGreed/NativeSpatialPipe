//! Role state machine for the AES67 PTP topology modes (DESIGN-TOPO.md
//! section 2): Init -> Listening -> {Slave, Gm, Local}; Slave carries the
//! Locked/Holdover sub-state in the session worker and Gm carries
//! Active/Passive in [super::gm].
//!
//! This module is pure: the caller passes the monotonic timestamp, whether a
//! valid in-domain Announce arrived, whether the slave follower has a master
//! or a fit, and the GM engine's BMCA verdict. It decides the reported role
//! and source; the runtime translates those into sockets and media clocks.
use super::{
    codec::ClockId,
    gm::{Dataset, StepReason},
};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum PtpMode {
    /// Strict follower, the pre-existing sender behavior (default).
    #[default]
    Slave,
    /// Listen first, then slave / own GM / local.
    Auto,
    /// Be the grandmaster.
    Gm,
    /// No PTP at all; local media clock.
    Local,
}

impl PtpMode {
    pub fn from_abi(value: u32) -> Option<Self> {
        match value {
            0 => Some(Self::Slave),
            1 => Some(Self::Auto),
            2 => Some(Self::Gm),
            3 => Some(Self::Local),
            _ => None,
        }
    }
    pub fn as_abi(self) -> u32 {
        match self {
            Self::Slave => 0,
            Self::Auto => 1,
            Self::Gm => 2,
            Self::Local => 3,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Slave => "slave",
            Self::Auto => "auto",
            Self::Gm => "gm",
            Self::Local => "local",
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Role {
    #[default]
    Init,
    Listening,
    Slave,
    Gm,
    /// GM stepped aside for a better foreign master (explicit gm mode only;
    /// auto reports Slave because the sender follows the external GM).
    Passive,
    Local,
}

impl Role {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Init => "init",
            Self::Listening => "listening",
            Self::Slave => "slave",
            Self::Gm => "gm",
            Self::Passive => "passive",
            Self::Local => "local",
        }
    }
    pub fn as_abi(self) -> u32 {
        match self {
            Self::Init => 0,
            Self::Listening => 1,
            Self::Slave => 2,
            Self::Gm => 3,
            Self::Passive => 4,
            Self::Local => 5,
        }
    }
    /// Roles whose media clock is the PTP epoch of a real GM.
    pub fn is_ptp(self) -> bool {
        matches!(self, Self::Slave | Self::Passive | Self::Gm)
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Source {
    #[default]
    None,
    ExternalGm,
    SelfGm,
    Local,
    Holdover,
    Passive,
}

impl Source {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::ExternalGm => "external-gm",
            Self::SelfGm => "self-gm",
            Self::Local => "local",
            Self::Holdover => "holdover",
            Self::Passive => "passive",
        }
    }
    pub fn as_abi(self) -> u32 {
        match self {
            Self::None => 0,
            Self::ExternalGm => 1,
            Self::SelfGm => 2,
            Self::Local => 3,
            Self::Holdover => 4,
            Self::Passive => 5,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Config {
    pub mode: PtpMode,
    /// Announcing a GM is allowed in auto mode when no external GM appears.
    pub gm_capable: bool,
    /// EUI-64 identity; None when the interface MAC could not be read.
    pub identity: Option<ClockId>,
    /// ptp_gm_utc_offset configured => our GM claims ptpTimescale=1.
    pub gm_time_traceable: bool,
    /// AUTO listening window (ptp_announce_timeout_ms).
    pub announce_timeout_ns: i128,
    /// Startup lock deadline (existing lock_timeout_ms).
    pub lock_timeout_ns: i128,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct AnnounceSeen {
    pub gm: ClockId,
    pub dataset: Dataset,
    pub time_traceable: bool,
}

/// Everything the state machine needs for one step. The session worker fills
/// this once per loop iteration.
#[derive(Clone, Copy, Debug, Default)]
pub struct Inputs {
    pub now: i128,
    /// A valid in-domain Announce arrived since the previous step.
    pub announce: Option<AnnounceSeen>,
    /// The follower has an accepted master (even before a clock fit exists).
    pub follower_master: Option<ClockId>,
    /// The follower produced an accepted clock fit.
    pub follower_locked: bool,
    /// The GM engine lost the BMCA and stepped aside; the reason and the
    /// winning foreign GM identity.
    pub gm_stepped_aside: Option<(StepReason, ClockId)>,
    /// The GM engine reclaimed after the foreign master timed out.
    pub gm_reclaimed: bool,
    /// The session holdover deadline elapsed with no clock fit (auto fallback).
    pub loss_holdover_expired: bool,
    /// The startup lock deadline elapsed with no clock fit.
    pub lock_timed_out: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Status {
    pub role: Role,
    pub source: Source,
    pub gm: Option<ClockId>,
    pub time_traceable: bool,
    pub reason: &'static str,
}

pub struct RoleMachine {
    config: Config,
    status: Status,
    listening_deadline: i128,
    /// Consecutive valid Announces, used for the EXTENSION hysteresis rules.
    announce_streak: u32,
    last_announce_at: Option<i128>,
    /// First moment the follower lost its master while in Slave.
    loss_since: Option<i128>,
    transitions: u64,
    own_identity: Option<ClockId>,
}

impl RoleMachine {
    pub fn new(config: Config, now: i128) -> Self {
        let own_identity = config.identity;
        let (role, source, reason) = match config.mode {
            PtpMode::Slave => (Role::Listening, Source::None, "listening_for_external_gm"),
            PtpMode::Auto => (Role::Listening, Source::None, "listening_for_external_gm"),
            PtpMode::Gm => {
                if own_identity.is_some() {
                    (Role::Gm, Source::SelfGm, "configured_gm")
                } else {
                    // Refused at open(); reported honestly if ever reached.
                    (Role::Local, Source::Local, "gm_identity_unavailable")
                }
            }
            PtpMode::Local => (Role::Local, Source::Local, "configured_local"),
        };
        Self {
            config,
            status: Status {
                role,
                source,
                gm: (role == Role::Gm).then_some(own_identity).flatten(),
                time_traceable: role == Role::Gm && config.gm_time_traceable,
                reason,
            },
            listening_deadline: now + config.announce_timeout_ns,
            announce_streak: 0,
            last_announce_at: None,
            loss_since: None,
            transitions: 0,
            own_identity,
        }
    }

    pub fn status(&self) -> Status {
        self.status
    }
    pub fn transitions(&self) -> u64 {
        self.transitions
    }
    pub fn role(&self) -> Role {
        self.status.role
    }

    fn set(
        &mut self,
        role: Role,
        source: Source,
        gm: Option<ClockId>,
        time_traceable: bool,
        reason: &'static str,
    ) {
        if self.status.role != role {
            self.transitions += 1;
        }
        self.status = Status {
            role,
            source,
            gm,
            time_traceable,
            reason,
        };
    }

    fn track_announce(&mut self, inputs: &Inputs) {
        match inputs.announce {
            Some(_) => {
                let consecutive = self
                    .last_announce_at
                    .is_none_or(|last| inputs.now - last <= self.config.announce_timeout_ns);
                self.announce_streak = if consecutive {
                    self.announce_streak.saturating_add(1)
                } else {
                    1
                };
                self.last_announce_at = Some(inputs.now);
            }
            None => {
                if self
                    .last_announce_at
                    .is_some_and(|last| inputs.now - last > self.config.announce_timeout_ns)
                {
                    self.announce_streak = 0;
                }
            }
        }
    }

    fn announce_recent(&self, now: i128) -> bool {
        self.last_announce_at
            .is_some_and(|last| now - last <= self.config.announce_timeout_ns)
    }

    /// Step the state machine. Returns true when the role, source or GM
    /// identity changed (the session then bumps the SDP version and the SAP
    /// announcement and logs the transition).
    pub fn step(&mut self, inputs: Inputs) -> bool {
        let before = self.status;
        self.track_announce(&inputs);
        match self.config.mode {
            PtpMode::Local => {
                self.set(Role::Local, Source::Local, None, false, "configured_local");
            }
            PtpMode::Slave => self.step_slave(&inputs),
            PtpMode::Gm => self.step_gm(&inputs),
            PtpMode::Auto => self.step_auto(&inputs),
        }
        self.status != before
    }

    fn step_slave(&mut self, inputs: &Inputs) {
        match self.status.role {
            Role::Listening | Role::Init => {
                if let Some(announce) = inputs.announce {
                    self.set(
                        Role::Slave,
                        Source::ExternalGm,
                        Some(announce.gm),
                        announce.time_traceable,
                        "external_gm",
                    );
                } else {
                    self.set(
                        Role::Listening,
                        Source::None,
                        None,
                        false,
                        "listening_for_external_gm",
                    );
                }
            }
            // Strict follower: no automatic mode change. Startup and loss are
            // handled by the session exactly like before this feature.
            _ => {}
        }
    }

    fn gm_fallback(&mut self, inputs: &Inputs, reason: &'static str) {
        match self.own_identity {
            Some(identity) => self.set(
                Role::Gm,
                Source::SelfGm,
                Some(identity),
                self.config.gm_time_traceable,
                reason,
            ),
            None => self.set(
                Role::Local,
                Source::Local,
                None,
                false,
                "gm_identity_unavailable",
            ),
        }
        self.loss_since = None;
        let _ = inputs;
    }

    fn local_fallback(&mut self, reason: &'static str) {
        self.set(Role::Local, Source::Local, None, false, reason);
        // A Local role must re-earn Slave through the EXTENSION hysteresis:
        // two consecutive valid Announce messages after the transition.
        self.announce_streak = 0;
        self.last_announce_at = None;
        self.loss_since = None;
    }

    fn step_gm(&mut self, inputs: &Inputs) {
        match self.status.role {
            Role::Gm | Role::Init => {
                if let Some((reason, winner)) = inputs.gm_stepped_aside {
                    // Explicit gm mode: step aside to Passive, never Slave.
                    self.set(
                        Role::Passive,
                        Source::Passive,
                        Some(winner),
                        inputs.announce.is_some_and(|announce| {
                            announce.gm == winner && announce.time_traceable
                        }),
                        match reason {
                            StepReason::BetterForeignGm => "better_foreign_gm",
                            StepReason::DualGm => "dual_gm_detected",
                        },
                    );
                } else if self.status.role != Role::Gm {
                    self.gm_fallback(inputs, "configured_gm");
                }
            }
            Role::Passive => {
                if inputs.gm_reclaimed {
                    self.gm_fallback(inputs, "foreign_gm_timeout_reclaim");
                } else if let Some(announce) = inputs.announce {
                    // Keep reporting the winning GM identity/time traceability.
                    self.set(
                        Role::Passive,
                        Source::Passive,
                        Some(announce.gm),
                        announce.time_traceable,
                        self.status.reason,
                    );
                }
            }
            _ => {}
        }
        self.loss_since = None;
    }

    fn step_auto(&mut self, inputs: &Inputs) {
        match self.status.role {
            Role::Init => {
                self.set(
                    Role::Listening,
                    Source::None,
                    None,
                    false,
                    "listening_for_external_gm",
                );
            }
            Role::Listening => {
                if let Some(announce) = inputs.announce {
                    // Step 2 of DESIGN-TOPO.md section 2: an external GM wins.
                    self.set(
                        Role::Slave,
                        Source::ExternalGm,
                        Some(announce.gm),
                        announce.time_traceable,
                        "external_gm",
                    );
                    self.loss_since = None;
                } else if inputs.now >= self.listening_deadline {
                    if self.config.gm_capable
                        && self.own_identity.is_some()
                        && self.config.identity.is_some()
                    {
                        self.gm_fallback(inputs, "announce_timeout_self_gm");
                    } else {
                        self.local_fallback("announce_timeout_local");
                    }
                }
            }
            Role::Slave => {
                if let Some(announce) = inputs.announce {
                    self.set(
                        Role::Slave,
                        Source::ExternalGm,
                        Some(announce.gm),
                        announce.time_traceable,
                        "external_gm",
                    );
                    self.loss_since = None;
                }
                if inputs.follower_master.is_some() && inputs.follower_locked {
                    self.loss_since = None;
                    self.set(
                        Role::Slave,
                        Source::ExternalGm,
                        self.status.gm.or(inputs.follower_master),
                        self.status.time_traceable,
                        "external_gm",
                    );
                } else if inputs.follower_master.is_none() && self.loss_since.is_some() {
                    self.set(
                        Role::Slave,
                        Source::Holdover,
                        self.status.gm,
                        self.status.time_traceable,
                        "holdover",
                    );
                } else if inputs.follower_master.is_none() && inputs.announce.is_none() {
                    self.loss_since = Some(inputs.now);
                    self.set(
                        Role::Slave,
                        Source::Holdover,
                        self.status.gm,
                        self.status.time_traceable,
                        "holdover",
                    );
                }
                if inputs.lock_timed_out && !inputs.follower_locked {
                    if self.announce_recent(inputs.now) && inputs.follower_master.is_some() {
                        // Announces keep arriving but no fit: fall back to the
                        // local clock instead of claiming a GM we cannot lock.
                        self.local_fallback("lock_timeout_local");
                    } else if self.config.gm_capable && self.own_identity.is_some() {
                        self.gm_fallback(inputs, "lock_timeout_self_gm");
                    } else {
                        self.local_fallback("lock_timeout_local");
                    }
                    return;
                }
                if inputs.loss_holdover_expired && inputs.follower_master.is_none() {
                    // Step 4: external -> self when capable, external -> local
                    // otherwise. Only reclaim GM after announce_timeout with no
                    // Announce (EXTENSION, avoids a dual GM while an external
                    // master is still present but unlocked).
                    if self.config.gm_capable
                        && self.own_identity.is_some()
                        && !self.announce_recent(inputs.now)
                    {
                        self.gm_fallback(inputs, "holdover_expired_self_gm");
                    } else if self.config.gm_capable && self.own_identity.is_some() {
                        self.local_fallback("holdover_expired_local_announce_present");
                    } else {
                        self.local_fallback("holdover_expired_local");
                    }
                }
            }
            Role::Gm => {
                if let Some((_, winner)) = inputs.gm_stepped_aside {
                    // Step 5: in auto the sender follows the external GM.
                    self.set(
                        Role::Slave,
                        Source::ExternalGm,
                        Some(winner),
                        inputs.announce.is_some_and(|announce| {
                            announce.gm == winner && announce.time_traceable
                        }),
                        "better_foreign_gm",
                    );
                    self.loss_since = None;
                } else if inputs.gm_reclaimed {
                    self.set(
                        Role::Gm,
                        Source::SelfGm,
                        self.own_identity,
                        self.config.gm_time_traceable,
                        "foreign_gm_timeout_reclaim",
                    );
                }
            }
            Role::Passive => {
                // Auto never reports Passive, but recover if it is entered.
                self.set(
                    Role::Gm,
                    Source::SelfGm,
                    self.own_identity,
                    self.config.gm_time_traceable,
                    "configured_gm",
                );
            }
            Role::Local => {
                if self.announce_streak >= 2 {
                    if let Some(announce) = inputs.announce {
                        // Step 6: local -> external after two consecutive
                        // announces (EXTENSION hysteresis).
                        self.set(
                            Role::Slave,
                            Source::ExternalGm,
                            Some(announce.gm),
                            announce.time_traceable,
                            "local_to_external_gm",
                        );
                        self.loss_since = None;
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::time::NS_PER_MS;

    const OUR_ID: ClockId = [0xae, 0x6f, 0x0d, 0xff, 0xfe, 0x8e, 0x86, 0xfc];
    const GM_ID: ClockId = [0x02, 0, 0, 0, 0, 0, 0, 9];

    fn config(mode: PtpMode, gm_capable: bool) -> Config {
        Config {
            mode,
            gm_capable,
            identity: Some(OUR_ID),
            gm_time_traceable: false,
            announce_timeout_ns: 100 * NS_PER_MS,
            lock_timeout_ns: 500 * NS_PER_MS,
        }
    }

    fn announce(time_traceable: bool) -> AnnounceSeen {
        AnnounceSeen {
            gm: GM_ID,
            dataset: Dataset {
                priority1: 128,
                clock_class: 248,
                clock_accuracy: 0xfe,
                offset_scaled_log_variance: 0xffff,
                priority2: 128,
                clock_identity: GM_ID,
            },
            time_traceable,
        }
    }

    fn inputs(now: i128) -> Inputs {
        Inputs {
            now,
            ..Inputs::default()
        }
    }

    #[test]
    fn auto_listens_then_follows_the_external_gm() {
        let mut machine = RoleMachine::new(config(PtpMode::Auto, true), 0);
        assert_eq!(machine.role(), Role::Listening);
        let mut step = inputs(10 * NS_PER_MS);
        step.announce = Some(announce(true));
        assert!(machine.step(step));
        assert_eq!(machine.role(), Role::Slave);
        assert_eq!(machine.status().source, Source::ExternalGm);
        assert_eq!(machine.status().gm, Some(GM_ID));
        assert!(machine.status().time_traceable);
    }

    #[test]
    fn auto_announce_timeout_becomes_gm_when_capable() {
        let mut machine = RoleMachine::new(config(PtpMode::Auto, true), 0);
        assert!(!machine.step(inputs(99 * NS_PER_MS)));
        assert_eq!(machine.role(), Role::Listening);
        assert!(machine.step(inputs(100 * NS_PER_MS)));
        assert_eq!(machine.role(), Role::Gm);
        assert_eq!(machine.status().source, Source::SelfGm);
        assert_eq!(machine.status().gm, Some(OUR_ID));
        assert_eq!(machine.status().reason, "announce_timeout_self_gm");
    }

    #[test]
    fn auto_announce_timeout_becomes_local_when_not_capable() {
        let mut machine = RoleMachine::new(config(PtpMode::Auto, false), 0);
        assert!(machine.step(inputs(100 * NS_PER_MS)));
        assert_eq!(machine.role(), Role::Local);
        assert_eq!(machine.status().source, Source::Local);
        assert_eq!(machine.status().gm, None);
        assert!(!machine.status().time_traceable);
        assert_eq!(machine.status().reason, "announce_timeout_local");
        // local mode never claims a PTP reference.
        assert!(!machine.status().role.is_ptp());
    }

    #[test]
    fn auto_slave_holdover_expiry_falls_back_to_gm_or_local() {
        for (capable, expected) in [(true, Role::Gm), (false, Role::Local)] {
            let mut machine = RoleMachine::new(config(PtpMode::Auto, capable), 0);
            let mut step = inputs(0);
            step.announce = Some(announce(true));
            machine.step(step);
            assert_eq!(machine.role(), Role::Slave);
            // Master disappears; holdover runs, then expires with no announce.
            let mut step = inputs(200 * NS_PER_MS);
            step.follower_master = None;
            step.loss_holdover_expired = true;
            machine.step(step);
            assert_eq!(machine.role(), expected, "capable={capable}");
            if capable {
                assert_eq!(machine.status().source, Source::SelfGm);
            } else {
                assert_eq!(machine.status().source, Source::Local);
            }
        }
    }

    #[test]
    fn auto_does_not_reclaim_gm_while_an_external_announce_is_present() {
        let mut machine = RoleMachine::new(config(PtpMode::Auto, true), 0);
        let mut step = inputs(0);
        step.announce = Some(announce(true));
        machine.step(step);
        // Lock lost but the external Announce keeps arriving: fall to local
        // rather than claim GM next to an external master.
        let mut step = inputs(200 * NS_PER_MS);
        step.announce = Some(announce(true));
        step.follower_master = None;
        step.loss_holdover_expired = true;
        machine.step(step);
        assert_eq!(machine.role(), Role::Local);
        assert_eq!(
            machine.status().reason,
            "holdover_expired_local_announce_present"
        );
    }

    #[test]
    fn auto_local_to_slave_needs_two_consecutive_announces() {
        let mut machine = RoleMachine::new(config(PtpMode::Auto, false), 0);
        machine.step(inputs(100 * NS_PER_MS));
        assert_eq!(machine.role(), Role::Local);
        let mut step = inputs(150 * NS_PER_MS);
        step.announce = Some(announce(false));
        machine.step(step);
        assert_eq!(machine.role(), Role::Local, "one announce is not enough");
        let mut step = inputs(200 * NS_PER_MS);
        step.announce = Some(announce(false));
        machine.step(step);
        assert_eq!(machine.role(), Role::Slave);
        assert_eq!(machine.status().source, Source::ExternalGm);
        assert!(!machine.status().time_traceable);
    }

    #[test]
    fn auto_gm_steps_aside_to_slave_on_a_better_foreign_announce() {
        let mut machine = RoleMachine::new(config(PtpMode::Auto, true), 0);
        machine.step(inputs(100 * NS_PER_MS));
        assert_eq!(machine.role(), Role::Gm);
        let mut step = inputs(200 * NS_PER_MS);
        step.announce = Some(announce(true));
        step.gm_stepped_aside = Some((StepReason::BetterForeignGm, GM_ID));
        machine.step(step);
        assert_eq!(machine.role(), Role::Slave);
        assert_eq!(machine.status().gm, Some(GM_ID));
        assert_eq!(machine.status().reason, "better_foreign_gm");
    }

    #[test]
    fn explicit_gm_is_immediate_and_passes_through_passive() {
        let mut machine = RoleMachine::new(config(PtpMode::Gm, true), 0);
        assert_eq!(machine.role(), Role::Gm);
        assert_eq!(machine.status().source, Source::SelfGm);
        assert_eq!(machine.status().gm, Some(OUR_ID));
        let mut step = inputs(1);
        step.announce = Some(announce(false));
        step.gm_stepped_aside = Some((StepReason::DualGm, GM_ID));
        machine.step(step);
        assert_eq!(machine.role(), Role::Passive);
        assert_eq!(machine.status().source, Source::Passive);
        assert_eq!(machine.status().gm, Some(GM_ID));
        assert_eq!(machine.status().reason, "dual_gm_detected");
        // Reclaim after the foreign master times out.
        let mut step = inputs(2);
        step.gm_reclaimed = true;
        machine.step(step);
        assert_eq!(machine.role(), Role::Gm);
        assert_eq!(machine.status().reason, "foreign_gm_timeout_reclaim");
    }

    #[test]
    fn explicit_slave_never_changes_mode_after_listening() {
        let mut machine = RoleMachine::new(config(PtpMode::Slave, true), 0);
        machine.step(inputs(NS_PER_MS));
        assert_eq!(machine.role(), Role::Listening);
        let mut step = inputs(2 * NS_PER_MS);
        step.announce = Some(announce(true));
        machine.step(step);
        assert_eq!(machine.role(), Role::Slave);
        // Loss, holdover expiry and lock timeout do not silently change the
        // mode: the session keeps the pre-existing LOCK_TIMEOUT behavior.
        let mut step = inputs(3 * NS_PER_MS);
        step.loss_holdover_expired = true;
        step.lock_timed_out = true;
        machine.step(step);
        assert_eq!(machine.role(), Role::Slave);
        assert_eq!(machine.status().source, Source::ExternalGm);
    }

    #[test]
    fn local_mode_is_a_fixed_role() {
        let mut machine = RoleMachine::new(config(PtpMode::Local, true), 0);
        assert_eq!(machine.role(), Role::Local);
        let mut step = inputs(NS_PER_MS);
        step.announce = Some(announce(true));
        machine.step(step);
        assert_eq!(machine.role(), Role::Local);
        assert_eq!(machine.status().source, Source::Local);
        assert!(!machine.status().role.is_ptp());
    }

    #[test]
    fn role_changes_are_counted_for_sdp_version_control() {
        let mut machine = RoleMachine::new(config(PtpMode::Auto, true), 0);
        let transitions = machine.transitions();
        assert!(machine.step(inputs(100 * NS_PER_MS)));
        assert_eq!(machine.transitions(), transitions + 1);
        // Repeating the same step does not bump the SDP version.
        assert!(!machine.step(inputs(101 * NS_PER_MS)));
        assert_eq!(machine.transitions(), transitions + 1);
    }
}
