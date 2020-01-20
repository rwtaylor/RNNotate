#!/usr/bin/env python
#
###############################################################################
# Author: Greg Zynda
# Last Modified: 09/05/2019
###############################################################################
# BSD 3-Clause License
# 
# Copyright (c) 2019, Texas Advanced Computing Center
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
###############################################################################

from operator import itemgetter
from pysam import FastaFile
import h5py, os, sys, logging
from time import time
logger = logging.getLogger(__name__)
import numpy as np
from teamRNN.util import irange, iterdict, fivenum, calcRegionBounds, bridge_array, is_reverse
from teamRNN.constants import few_gff3_f2i, few_gff3_i2f, gff3_f2i, gff3_i2f, \
	contexts, strands, base2index, te_feature_names, few_te_feature_names
from teamRNN.constants import te_order_f2i, te_order_i2f, te_sufam_f2i, te_sufam_i2f
from itertools import chain
import re
from quicksect import IntervalTree
from glob import glob
from collections import defaultdict as dd
from itertools import izip

class output_aggregator:
	'''
	>>> OA = output_aggregator(chrom_dict)
	>>> OA.vote(chrom, s, e, out_array)
	>>> OA.write_gff3()
	'''
	def __init__(self, fasta_file, noTEMD=False, h5_file='tmp_vote.h5', stranded=False, few=False):
		self.fasta_file = fasta_file
		self.noTEMD = noTEMD
		self.few = few
		self.i2f = few_gff3_i2f if self.few else gff3_i2f
		self.f2i = few_gff3_f2i if self.few else gff3_f2i
		self.stranded = stranded
		with FastaFile(fasta_file) as FA:
			self.chrom_dict = {c:FA.get_reference_length(c) for c in FA.references}
		self.cur_chrom = ''
		self.h5_file = h5_file
		self.H5 = h5py.File(h5_file, 'a')
		self._genome_init()
	def __del__(self):
		if self.H5:
			self.H5.close()
			os.remove(self.h5_file)
	def close(self):
		self.__del__()
	def _load_arrays(self, chrom):
		s_time = time()
		if chrom == self.cur_chrom:
			return
		self.feature_vote_array = self._swap_dset(self.cur_chrom, chrom, \
			'/votes/features', self.feature_vote_array)
		self.feature_total_array = self._swap_dset(self.cur_chrom, chrom, \
			'/totals/features', self.feature_total_array)
		if not self.noTEMD:
			self.te_order_array = self._swap_dset(self.cur_chrom, chrom, \
				'/votes/tes/order', self.te_order_array)
			self.te_sufam_array = self._swap_dset(self.cur_chrom, chrom, \
				'/votes/tes/sufam', self.te_sufam_array)
		#self.te_total_array = self.H5[chrom+'/totals/tes']
		logger.debug("Took %i seconds to swap from %s to %s"%(int(time()-s_time), self.cur_chrom, chrom))
		self.cur_chrom = chrom
	def _swap_dset(self, old_c, new_c, suffix, old_a):
		old_name = old_c+suffix
		new_name = new_c+suffix
		assert(old_a.shape == self.H5[old_name].shape)
		self.H5[old_name].write_direct(old_a)
		new_a = np.zeros(self.H5[new_name].shape, dtype=self.H5[new_name].dtype)
		self.H5[new_name].read_direct(new_a)
		return new_a
	def _create_dset(self, size_tuple, name, dtype=np.uint32):
		self.H5.create_dataset(name, size_tuple, compression='gzip', compression_opts=6, \
			chunks=True, fillvalue=0, dtype=dtype)
		return np.zeros(size_tuple, dtype=dtype)
	def _genome_init(self):
		n_features = len(self.i2f)
		n_order_ids = len(te_order_i2f)
		n_sufam_ids = len(te_sufam_i2f)
		for chrom, chrom_len in iterdict(self.chrom_dict):
			# total != sum
			self.feature_vote_array = self._create_dset((chrom_len, n_features), \
				chrom+'/votes/features')
			self.feature_total_array = self._create_dset((chrom_len, 1), \
				chrom+'/totals/features')
			if not self.noTEMD:
				# These do not need a total array since there is only a single value per location
				self.te_order_array = self._create_dset((chrom_len, n_order_ids), \
					chrom+'/votes/tes/order')
				self.te_sufam_array = self._create_dset((chrom_len, n_sufam_ids), \
					chrom+'/votes/tes/sufam')
		self.cur_chrom = chrom
		# Init this
		if self.few:
			self.te_feature_ids = set([self.f2i[s+f] for f in few_te_feature_names for s in '+-'])
		else:
			self.te_feature_ids = set([self.f2i[s+f] for f in te_feature_names for s in '+-'])
		# Create counters
		self.features_tp = np.zeros(n_features)
		self.features_fn = np.zeros(n_features)
		self.features_fp = np.zeros(n_features)
		if not self.noTEMD:
			self.te_order_tp = np.zeros(n_order_ids)
			self.te_order_fn = np.zeros(n_order_ids)
			self.te_order_fp = np.zeros(n_order_ids)
			self.te_sufam_tp = np.zeros(n_sufam_ids)
			self.te_sufam_fn = np.zeros(n_sufam_ids)
			self.te_sufam_fp = np.zeros(n_sufam_ids)
	def vote(self, chrom, start, end, array, overwrite=False, reverse=False):
		#print "VOTE:", chrom, start, end, np.nonzero(array)
		# Split the array
		if not self.noTEMD:
			assert(array.shape[1] == len(self.i2f)+2)
			te_order_array = array[:,-2]
			te_sufam_array = array[:,-1]
		else:
			assert(array.shape[1] == len(self.i2f))
		n_feat = len(self.i2f)
		half_feat = n_feat/2
		assert(2*half_feat == n_feat)
		if self.stranded:
			if not reverse:
				feature_array = array[:,:half_feat]
			else:
				feature_array = array[::-1,half_feat:n_feat]
		else:
			feature_array = array[:,:len(self.i2f)]
		# Load the current chromosome arrays
		if self.cur_chrom != chrom: self._load_arrays(chrom)
		# Track features
		#print "BEFORE", self.feature_total_array[start:end].flatten()
		if overwrite:
			self.feature_total_array[start:end] = 1
		else:
			self.feature_total_array[start:end] += 1
		#print "AFTER", self.feature_total_array[start:end].flatten()
		if np.sum(feature_array):
			if overwrite:
				if self.stranded:
					if not reverse:
						self.feature_vote_array[start:end,:half_feat] = feature_array
					else:
						self.feature_vote_array[start:end,half_feat:n_feat] = feature_array
				else:
					self.feature_vote_array[start:end] = feature_array
			else:
				if self.stranded:
					if not reverse:
						self.feature_vote_array[start:end,:half_feat] += feature_array
					else:
						self.feature_vote_array[start:end,half_feat:n_feat] += feature_array
				else:
					self.feature_vote_array[start:end] += feature_array
		if not self.noTEMD:
			# Track te class/family
			if sum(te_order_array):
				for i,v in enumerate(te_order_array):
					if overwrite:
						self.te_order_array[start+i,v] = 1
					else:
						self.te_order_array[start+i,v] += 1
			if sum(te_sufam_array):
				for i,v in enumerate(te_sufam_array):
					if overwrite:
						self.te_sufam_array[start+i,v] = 1
					else:
						self.te_sufam_array[start+i,v] += 1
	def compare(self, chrom, start, end, pred_array, true_array):
		if not self.noTEMD:
			assert(array.shape[1] == len(self.i2f)+2)
			pred_te_order_array = pred_array[:,-2]
			pred_te_sufam_array = pred_array[:,-1]
			true_te_order_array = true_array[:,-2]
			true_te_sufam_array = true_array[:,-1]
		else:
			assert(array.shape[1] == len(self.i2f))
		assert(pred_array.shape == true_array.shape)
		# Split the array
		pred_feature_array = pred_array[:,:len(self.i2f)]
		true_feature_array = true_array[:,:len(self.i2f)]
		# Track features
		for i in irange(pread_array.shape[0]):
			for j in irange(len(self.i2f)):
				# Features
				if pred_feature_array[i,j] != true_feature_array[i,j]:
					if pred_feature_array[i,j] == 1:
						self.features_fp[j] += 1
					else:
						self.features_fn[j] += 1
				elif pred_feature_array[i,j] == 1:
					self.features_tp[j] += 1
					# TEs
					if j in self.te_feature_ids and not self.noTEMD:
						# Order
						if true_te_order_array[i] == 0:
							if pred_te_order_array[i] != 0:
								self.te_order_fp[pred_te_order_array[i]] += 1
						else:
							if pred_te_order_array[i] == true_te_order_array[i]:
								self.te_order_tp[pred_te_order_array[i]] += 1
							else:
								self.te_order_fn[pred_te_order_array[i]] += 1
						# Super family
						if true_te_sufam_array[i] == 0:
							if pred_te_sufam_array[i] != 0:
								self.te_sufam_fp[pred_te_sufam_array[i]] += 1
						else:
							if pred_te_sufam_array[i] == true_te_sufam_array[i]:
								self.te_sufam_tp[pred_te_sufam_array[i]] += 1
							else:
								self.te_sufam_fn[pred_te_sufam_array[i]] += 1
	def write_gff3(self, out_file='', threshold=0.5, min_size=0, max_fill_size=0):
		total_feature_count = 0
		out_gff3 = ['##gff-version   3']
		if os.path.exists(out_file):
			logger.info("Overwriting old %s"%(out_file))
			os.remove(out_file)
		if min_size or max_fill_size:
			logger.info("Filling gaps <= %i and Removing |features| < %i"%(max_fill_size, min_size))
		for chrom in sorted(self.chrom_dict.keys()):
			chrom_len = self.chrom_dict[chrom]
			features = []
			se_array = [[0,0] for i in irange(len(self.i2f))]
			self._load_arrays(chrom)
			logger.debug("Feature vote row sums: %s"%(str(list(self.feature_vote_array.sum(axis=0)))))
			logger.debug("Feature total row sums: %s"%(str(list(self.feature_total_array.sum(axis=0)))))
			for feat_index in self.i2f.keys():
				vote_array = self.feature_vote_array[:,feat_index]
				if self.stranded:
					gtT_mask = vote_array >= threshold*self.feature_total_array[:,0]/2.0
				else:
					gtT_mask = vote_array >= threshold*self.feature_total_array[:,0]
				gtZ_mask = vote_array > 0
				mask = np.logical_and(gtT_mask, gtZ_mask)
				if min_size or max_fill_size:
					bridge_array(mask, min_size, max_fill_size)
				bound_array = calcRegionBounds(mask, inclusive=True)+1
				for s,e in bound_array:
					features.append((s,e,feat_index))
			features.sort(key=itemgetter(0,1))
			for s,e,feat_index in features:
				full_name = self.i2f[feat_index]
				strand = full_name[0]
				feature_name = full_name[1:]
				feature_str = "%s\tteamRNN\t%s\t%i\t%i\t.\t%s\t.\tID=team_%i"%(chrom, feature_name, s, e, strand, total_feature_count)
				if feature_name in te_feature_names and not self.noTEMD:
					argmax_order_sum = np.argmax(np.sum(self.te_order_array[s-1:e], axis=0))
					te_order = te_order_i2f[argmax_order_sum]
					argmax_sufam_sum = np.argmax(np.sum(self.te_sufam_array[s-1:e], axis=0))
					te_sufam = te_sufam_i2f[argmax_sufam_sum]
					feature_str += ';Order=%s;Superfamily=%s'%(te_order, te_sufam)
				out_gff3.append(feature_str)
				total_feature_count += 1
			logger.info("Finished writing %s"%(chrom))
		if out_file:
			with open(out_file,'w') as OF:
				OF.write('\n'.join(out_gff3)+'\n')
		else:
			return out_gff3

class MSE_interval:
	def __init__(self, fasta_file, out_dir, hvd_rank):
		self.mse_dict = dd(IntervalTree)
		self.rank = hvd_rank
		self.out_dir = out_dir
		self.regex = re.compile(r"mse__([>\w]+)__(\d+)\.")
		self.fasta_file = fasta_file
		with FastaFile(fasta_file) as FA:
			self.chrom_dict = {c:FA.get_reference_length(c) for c in FA.references}
		self.mse_array_dict = {}
		self.mse_count_dict = {}
		self.agg_method = {'median':np.median, 'mean':np.mean, 'sum':np.sum}
		self.c_method = {'midpoint':self._to_midpoint, 'range':self._to_range}
		self.dumped = False
		self.loaded = False
	def add_batch(self, cb, mse_value):
		for chrom, s, e in cb:
			self.mse_dict[chrom].add(s,e,mse_value)
	def add_batch_array(self, cb, mse_value):
		for chrom, s, e in cb:
			self._add_array_value(c, s, e, mse_value)
	def add_predict_batch(self, cb, yb, ypb):
		mse_list, first_chrom = [], cb[0][0]
		for c, y, yp in izip(cb, yb, ypb):
			chrom, s, e = c
			mse = np.square(y - yp).mean()
			#print c, y, yp, mse
			mse_list.append(mse)
			self._add_array_value(chrom, s, e, mse)
		#s, e = cb[0][1], cb[-1][2]
		#fns = map(str,fivenum(mse_list))
		#logger.debug("%s:%i-%i contained the following MSE distribution [%s]"%(chrom, s, e, ', '.join(fns)))
	def _add_array_value(self, chrom, s, e, v):
		if chrom not in self.mse_array_dict:
			self._create_mse_array(chrom)
		self.mse_array_dict[chrom][s:e] += v
		self.mse_count_dict[chrom][s:e] += 1
	def dump(self):
		if not os.path.exists(self.out_dir): os.makedirs(self.out_dir)
		for chrom in self.mse_dict:
			out_name = os.path.join(self.out_dir, 'mse__%s__%i.pkl'%(chrom, self.rank))
			self.mse_dict[chrom].dump(out_name)
		for chrom in self.mse_array_dict:
			out_name = os.path.join(self.out_dir, 'mse__%s__%i.npz'%(chrom, self.rank))
			np.savez_compressed(out_name, v=self.mse_array_dict[chrom], \
				c=self.mse_array_dict[chrom])
		self.dumped = True
	def _create_mse_array(self, chrom):
		nbases = self.chrom_dict[chrom]
		self.mse_array_dict[chrom] = np.zeros(nbases, dtype=np.float)
		self.mse_count_dict[chrom] = np.zeros(nbases, dtype=np.uint16)
	def __del__(self):
		self.close()
	def close(self):
		for fname in glob("%s/mse__*__%i.*"%(self.out_dir, self.rank)):
			logger.debug("Deleting %s"%(fname))
			os.remove(fname)
	def load_all(self):
		if self.dumped and not self.loaded and self.rank == 0:
			for p_file in glob("%s/mse__*__*.pkl"%(self.out_dir)):
				chrom, rank = self.regex.search(p_file).groups()
				rank = int(rank)
				assert(chrom in self.chrom_dict.keys())
				if rank != self.rank:
					logger.debug("loading %s"%(p_file))
					self.mse_dict[chrom].load(p_file)
				else:
					logger.debug("skipping %s"%(p_file))
			for npy_file in glob("%s/mse__*__*.npz"%(self.out_dir)):
				chrom, rank = self.regex.search(npy_file).groups()
				assert(chrom in self.chrom_dict.keys())
				if chrom not in self.mse_array_dict:
					self._create_mse_array(chrom)
				rank = int(rank)
				if rank != self.rank:
					logger.debug("loading %s"%(npy_file))
					loaded = np.load(npy_file)
					self.mse_array_dict[chrom] += loaded['v'].astype(np.float)
					self.mse_count_dict[chrom] += loaded['c'].astype(np.uint16)
				else:
					logger.debug("skipping %s"%(npy_file))
			self.loaded = True
	def _to_midpoint(self, s, e, v):
		half = (e-s)/2.0
		x, y = [s+half], [v]
		return x,y
	def _to_range(self, s, e, v):
		x, y = [s, e], [v, v]
		return x,y
	def _region_to_agg_value(self, chrom, start, end, method='mean'):
		values = []
		if chrom in self.mse_dict:
			for interval in self.mse_dict[chrom].search(start, end):
				v = interval.data
				assert(not hasattr(v, '__iter__'))
				N = min(end, interval.end)-max(start, interval.start)
				values += [v]*N
		elif chrom in self.mse_array_dict:
				vals = self.mse_array_dict[chrom][start:end]
				counts = self.mse_count_dict[chrom][start:end]
				#print chrom, start, end, vals
				if sum(counts) == 0: return -1.0
				if method == 'mean':
					return vals.sum()/counts.sum()
				elif method == 'sum':
					return vals.sum()
		else:
			return -1
		#print chrom, start, end, values
		agg_value = self.agg_method[method](values) if len(values) else -1
		return agg_value
	def _region_to_xy(self, c, s, e, method='mean', coords='midpoint'):
		agg_value = self._region_to_agg_value(c, s, e, method)
		rx, ry = self.c_method[coords](s, e, agg_value)
		return rx, ry
	def to_array(self, chrom, width=1000, method='mean', coords='midpoint'):
		x,y = [], []
		if self.rank == 0:
			for start in irange(0, self.chrom_dict[chrom], width):
				end = min(start+width, self.chrom_dict[chrom])
				assert(start != end)
				rx, ry = self._region_to_xy(chrom, start, end, method, coords)
				x += rx
				y += ry
		return x,y
	def write(self, hvd=False, chroms=[], name='TRAIN', epoch=0, width=1000, method='mean', coords='midpoint'):
		assert(name in set(('TRAIN','TEST')))
		if hvd:
			self.dump()
			hvd.allgather([self.rank], name="Barrier")
			self.load_all()
			hvd.allgather([self.rank], name="Barrier")
		if self.rank != 0: return
		for chrom in sorted(chroms):
			x,y = self.to_array(chrom, width=width, method=method, coords=coords)
			f_sum_str_list = map(str, fivenum(y))
			f_sum_str = ', '.join(f_sum_str_list)
			logger.info("%s - Epoch %3i - Chrom %s MSE summary [%s]"%(name, epoch+1, chrom, f_sum_str))
			out_file = os.path.join(self.out_dir, '%s_e%i_%s.npz'%(name.lower(), epoch, chrom))
			np.savez(out_file, x=x, y=y)
			logger.debug("Wrote %s"%(out_file))

#def main():

#if __name__ == "__main__":
#	main()
