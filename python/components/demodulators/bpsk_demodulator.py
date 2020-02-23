#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2019 Daniel Estevez <daniel@destevez.net>.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# 
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software; see the file COPYING.  If not, write to
# the Free Software Foundation, Inc., 51 Franklin Street,
# Boston, MA 02110-1301, USA.

from gnuradio import gr, analog, blocks, digital, filter
from gnuradio.filter import firdes
from math import ceil, pi
from ...hier.rms_agc import rms_agc
from ...utils.options_block import options_block
from ... import manchester_sync
import pathlib

class bpsk_demodulator(gr.hier_block2, options_block):
    """
    Hierarchical block to demodulate BPSK.

    The input can be either IQ or real.

    Args:
        baudrate: Baudrate in symbols per second (float)
        sample_rate: Sample rate in samples per second (float)
        iq: Whether the input is IQ or real (bool)
        f_offset: Frequency offset in Hz (float)
        differential: Perform non-coherent DBPSK decoding (bool)
        manchester: Use Manchester coding (bool)
        dump_path: Path to dump internal signals to files (str)
        options: Options from argparse
    """
    def __init__(self, baudrate, samp_rate, iq, f_offset = None, differential = False, manchester = False, dump_path = None, options = None):
        gr.hier_block2.__init__(self, "bpsk_demodulator",
            gr.io_signature(1, 1, gr.sizeof_gr_complex if iq else gr.sizeof_float),
            gr.io_signature(1, 1, gr.sizeof_float))
        options_block.__init__(self, options)

        if dump_path is not None:
            dump_path = pathlib.Path(dump_path)

        if manchester:
            baudrate *= 2
        sps = samp_rate / baudrate
        max_sps = 10
        if sps > max_sps:
            decimation = ceil(sps / max_sps)
        else:
            decimation = 1
        sps /= decimation

        filter_cutoff = baudrate * 2.0
        filter_transition = baudrate * 0.2

        if f_offset is None:
            f_offset = self.options.f_offset
        if f_offset is None:
            if iq:
                f_offset = 0
            elif baudrate <= 2400:
                f_offset = 1500
            else:
                f_offset = 12000

        taps = firdes.low_pass(1, samp_rate, filter_cutoff, filter_transition)
        f = filter.freq_xlating_fir_filter_ccf if iq else filter.freq_xlating_fir_filter_fcf
        self.xlating = f(decimation, taps, f_offset, samp_rate)

        agc_constant = 2e-2 / sps # This gives a time constant of 50 symbols
        self.agc = rms_agc(agc_constant, 1)

        if dump_path is not None:
            self.agc_in = blocks.file_sink(gr.sizeof_gr_complex, str(dump_path / 'agc_in.c64'), False)
            self.agc_out = blocks.file_sink(gr.sizeof_gr_complex, str(dump_path / 'agc_out.c64'), False)
            self.connect(self.xlating, self.agc_in)
            self.connect(self.agc, self.agc_out)

        if not self.options.disable_fll:
            fll_bw = 2*pi*decimation/samp_rate*self.options.fll_bw
            self.fll = digital.fll_band_edge_cc(sps, self.options.rrc_alpha, 100, fll_bw)

            if dump_path is not None:
                self.fll_freq = blocks.file_sink(gr.sizeof_float, str(dump_path / 'fll_freq.f32'), False)
                self.fll_phase = blocks.file_sink(gr.sizeof_float, str(dump_path / 'fll_phase.f32'), False)
                self.fll_error = blocks.file_sink(gr.sizeof_float, str(dump_path / 'fll_error.f32'), False)
                self.connect((self.fll, 1), self.fll_freq)
                self.connect((self.fll, 2), self.fll_phase)
                self.connect((self.fll, 3), self.fll_error)            

        filter_cutoff2 = baudrate * 1.0
        filter_transition2 = baudrate * 0.1
        taps2 = firdes.low_pass(1, samp_rate/decimation, filter_cutoff2, filter_transition2)
        self.lowpass = filter.fir_filter_ccf(1, taps2)
        
        nfilts = 16
        rrc_taps = firdes.root_raised_cosine(nfilts, nfilts, 1.0/float(sps), self.options.rrc_alpha, int(ceil(11*sps*nfilts)))
        clk_bw = 2*pi/sps*self.options.clk_bw
        ted_gain = 0.5/sps # "empiric" formula for TED gain of a PFB MF TED for complex BPSK
        damping = 1.0
        self.clock_recovery = digital.symbol_sync_cc(digital.TED_SIGNAL_TIMES_SLOPE_ML,
                                                     sps,
                                                     clk_bw,
                                                     damping,
                                                     ted_gain,
                                                     self.options.clk_limit,
                                                     1,
                                                     digital.constellation_bpsk().base(),
                                                     digital.IR_PFB_MF,
                                                     nfilts,
                                                     rrc_taps)
        
        if dump_path is not None:
            self.clock_recovery_out = blocks.file_sink(gr.sizeof_gr_complex, str(dump_path / 'clock_recovery_out.c64'), False)
            self.clock_recovery_err = blocks.file_sink(gr.sizeof_float, str(dump_path / 'clock_recovery_err.f32'), False)
            self.clock_recovery_T_inst = blocks.file_sink(gr.sizeof_float, str(dump_path / 'clock_recovery_T_inst.f32'), False)
            self.clock_recovery_T_avg = blocks.file_sink(gr.sizeof_float, str(dump_path / 'clock_recovery_T_avg.f32'), False)
            self.connect(self.clock_recovery, self.clock_recovery_out)
            self.connect((self.clock_recovery, 1), self.clock_recovery_err)
            self.connect((self.clock_recovery, 2), self.clock_recovery_T_inst)
            self.connect((self.clock_recovery, 3), self.clock_recovery_T_avg)

        self.connect(self, self.xlating, self.agc)
        if self.options.disable_fll:
            self.connect(self.agc, self.lowpass)
        else:
            self.connect(self.agc, self.fll, self.lowpass)
        self.connect(self.lowpass, self.clock_recovery)

        self.complex_to_real = blocks.complex_to_real(1)

        if manchester:
            self.manchester = manchester_sync(self.options.manchester_history)
            self.connect(self.clock_recovery, self.manchester)
        else:
            self.manchester = self.clock_recovery
                
        if differential:
            self.delay = blocks.delay(gr.sizeof_gr_complex, 1)
            self.multiply_conj = blocks.multiply_conjugate_cc(1)
            sign = -1 if manchester else 1
            self.multiply_const = blocks.multiply_const_ff(sign, 1) # take care about inverion in Manchester
            self.connect(self.manchester, (self.multiply_conj, 0))
            self.connect(self.manchester, self.delay, (self.multiply_conj, 1))
            self.connect(self.multiply_conj, self.complex_to_real, self.multiply_const, self)
        else:
            costas_bw = 2*pi/baudrate*self.options.costas_bw
            self.costas = digital.costas_loop_cc(costas_bw, 2, False)

            if dump_path is not None:
                self.costas_out = blocks.file_sink(gr.sizeof_gr_complex, str(dump_path / 'costas_out.c64'), False)
                self.costas_frequency = blocks.file_sink(gr.sizeof_float, str(dump_path / 'costas_frequency.f32'), False)
                self.costas_phase = blocks.file_sink(gr.sizeof_float, str(dump_path / 'costas_phase.f32'), False)
                self.costas_error = blocks.file_sink(gr.sizeof_float, str(dump_path / 'costas_error.f32'), False)
                self.connect(self.costas, self.costas_out)
                self.connect((self.costas, 1), self.costas_frequency)
                self.connect((self.costas, 2), self.costas_phase)
                self.connect((self.costas, 3), self.costas_error)
            
            self.connect(self.manchester, self.costas, self.complex_to_real, self)

    _default_rrc_alpha = 0.35
    _default_fll_bw = 25
    _default_clk_rel_bw = 0.02
    _default_clk_limit = 0.02
    _default_costas_bw = 50
    _default_manchester_history = 32
    
    @classmethod
    def add_options(cls, parser):
        """
        Adds BPSK demodulator specific options to the argparse parser
        """
        parser.add_argument('--f_offset', type = float, default = None, help = 'Frequency offset (Hz) [default=1500 or 12000]')
        parser.add_argument('--rrc_alpha', type = float, default = cls._default_rrc_alpha, help = 'RRC roll-off (Hz) [default=%(default)r]')
        parser.add_argument('--disable_fll', action = 'store_true', help = 'Disable FLL')
        parser.add_argument('--fll_bw', type = float, default = cls._default_fll_bw, help = 'FLL bandwidth (Hz) [default=%(default)r]')
        parser.add_argument('--clk_bw', type = float, default = cls._default_clk_rel_bw, help = 'Clock recovery bandwidth (relative to baudrate) [default=%(default)r]')
        parser.add_argument('--clk_limit', type = float, default = cls._default_clk_limit, help = 'Clock recovery limit (relative to baudrate) [default=%(default)r]')
        parser.add_argument('--costas_bw', type = float, default = cls._default_costas_bw, help = 'Costas loop bandwidth (Hz) [default=%(default)r]')
        parser.add_argument('--manchester_history', type = int, default = cls._default_manchester_history, help = 'Manchester recovery history (symbols) [default=%(default)r]')