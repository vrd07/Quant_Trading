"""Causal wavelet / spectral cycle-timing engine.

Research and signal-timing only. Every public function is a pure function of a
trailing window: no stage may read a sample newer than the bar being evaluated.
"""
