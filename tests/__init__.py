import unittest, sys, os

os.environ['PYTHONHASHSEED']='42'
os.environ['TF_DETERMINISTIC_OPS']='1'

from glob import glob
from time import time
try:
	from StringIO import StringIO
except:
	from io import StringIO
# buffer for capturing log info
logStream = StringIO()
# Need to start logger BEFORE importing any pyPlateCalibrate code
import logging
FORMAT = "[%(levelname)s - %(filename)s:%(lineno)s - %(funcName)15s] %(message)s"
logging.basicConfig(stream=logStream, level=logging.DEBUG, format=FORMAT)
import teamRNN
from teamRNN import reader, constants, writer, model, util
from pysam import FastaFile
from shutil import rmtree
import numpy as np
from Meth5py import Meth5py
try:
	from unittest.mock import patch
except:
	from mock import patch
from glob import glob

class TestReader(unittest.TestCase):
	def setUp(self):
		tpath = os.path.dirname(__file__)
		self.fa = os.path.join(tpath, 'test.fa')
		self.fai = os.path.join(tpath, 'test.fa.fai')
		self.gff3 = os.path.join(tpath, 'test.gff3')
		self.mr1 = os.path.join(tpath, 'test_meth.txt')
		self.n_inputs = 10
		self.seq_len = 15
		self.n_outputs = len(constants.gff3_f2i)+2
		self.test_model = True
		self.n_epoch = 100
		self.learning_rate = 0.01
		m5 = Meth5py(self.mr1, self.fa, n_cores=1)
		m5.close()
		del m5
		GI = reader.gff3_interval(self.gff3, force=True)
		del GI
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3)
		del IS
	def tearDown(self):
		## Runs after every test function ##
		# Wipe log
		logStream.truncate(0)
		if os.path.exists('mse_tmp'): rmtree('mse_tmp')
		map(os.remove, glob('*_s*png'))
		## Runs after every test function ##
	def _compare_against_file(self, out_lines, in_file, noTEMD=False):
		self.assertTrue(os.path.exists(in_file))
		with open(in_file,'r') as GFF3:
			input_lines = GFF3.readlines()
		self.assertEqual(len(input_lines), len(out_lines))
		for ol, fl in zip(out_lines, input_lines):
			if ol[0] != '#':
				ols = ol.rstrip('\n').split('\t')[:9]
				ols[1] = 'test'
				if noTEMD:
					fls = fl.rstrip('\n').split(';')[0].split('\t')[:9]
				else:
					fls = fl.rstrip('\n').split('\t')[:9]
				self.assertEqual(ols, fls)
	def test_calcRegionBounds_inclusive(self):
		IA = np.array([1,1,0,0,1,1,1,0,0,1,1], dtype=np.bool)
		bounds = util.calcRegionBounds(IA, inclusive=True)
		self.assertEqual(list(bounds[0]), [0,1])
		self.assertEqual(list(bounds[1]), [4,6])
		self.assertEqual(list(bounds[2]), [9,10])
		self.assertEqual(bounds.shape, (3,2))
	def test_calcRegionBounds_exclusive(self):
		IA = np.array([1,1,0,0,1,1,1,0,0,1,1], dtype=np.bool)
		bounds = util.calcRegionBounds(IA)
		self.assertEqual(list(bounds[0]), [0,2])
		self.assertEqual(list(bounds[1]), [4,7])
		self.assertEqual(list(bounds[2]), [9,11])
		self.assertEqual(bounds.shape, (3,2))
	def test_calcRegionBounds_exclusive_zero(self):
		IA = np.array([1,1,0,0,1,1,1,0,0,1,1], dtype=np.bool)
		bounds = util.calcRegionBounds(IA, null=1)
		self.assertEqual(list(bounds[0]), [2,4])
		self.assertEqual(list(bounds[1]), [7,9])
		self.assertEqual(bounds.shape, (2,2))
	def test_bridge_array(self):
		ORIG = np.array([1,1,0,0,1,1,1,0,0,0,1,1], dtype=np.bool)
		IA = np.copy(ORIG)
		util.bridge_array(IA, min_size=1, max_gap_size=1)
		self.assertEqual(list(IA), [1,1,0,0,1,1,1,0,0,0,1,1])
		IA = np.copy(ORIG)
		util.bridge_array(IA, min_size=2, max_gap_size=2)
		self.assertEqual(list(IA), [1,1,1,1,1,1,1,0,0,0,1,1])
		IA = np.copy(ORIG)
		util.bridge_array(IA, min_size=3, max_gap_size=2)
		self.assertEqual(list(IA), [1,1,1,1,1,1,1,0,0,0,0,0])
		IA = np.copy(ORIG)
		util.bridge_array(IA, min_size=4, max_gap_size=3)
		self.assertEqual(list(IA), [1,1,1,1,1,1,1,1,1,1,1,1])
	def test_refcache(self):
		RC = reader.refcache(self.fa)
		FA = FastaFile(self.fa)
		for chrom in FA.references:
			chrom_len = FA.get_reference_length(chrom)
			chrom_seq = FA.fetch(chrom)
			self.assertEqual(chrom_seq, RC.fetch(chrom, 0, chrom_len))
			for i in range(chrom_len-3):
				self.assertEqual(RC.fetch(chrom, i, i+3), FA.fetch(chrom, i, i+3))
		# clean up
		FA.close()
	def test_split2quality(self):
		self.assertEqual(reader._split2quality('> dna:chromosome bears'.split(' ')), 3)
		self.assertEqual(reader._split2quality('> dna:scaffold'.split(' ')), 2)
		self.assertEqual(reader._split2quality('> dna:supercontig'.split(' ')), 1)
		self.assertEqual(reader._split2quality('> dna:contig hello there'.split(' ')), 1)
		self.assertEqual(reader._split2quality('>cats'.split(' ')), 0)
		self.assertEqual(reader._split2quality('>cats and dogs'.split(' ')), 0)
	def test_refcache_quality(self):
		RC = reader.refcache(self.fa)
		FA = FastaFile(self.fa)
		for chrom in FA.references:
			self.assertEqual(RC.chrom_qualities[chrom], 3)
	def test_rev_comp(self):
		IS = reader.input_slicer(self.fa, self.mr1, stateful=True)
		XL = [x for c,x in IS.stateful_chrom_iter('Chr1', seq_len=5, batch_size=2)]
		#00222202033333021300
		#AATTTTATACCCCCATGCAA
		#11111 22222
		#     11111 22222
		RXL = map(reader.rev_comp, XL)
		for xb, rxb in zip(XL, RXL):
			self.assertTrue(np.all(xb[:,:,1:] == np.flip(rxb[:,:,1:], axis=1)))
			self.assertFalse(np.all(xb[:,:,0] == np.flip(rxb[:,:,0], axis=1)))
		self.assertTrue(np.all(RXL[0][0,:,0] == [0,0,0,2,2]))
		self.assertTrue(np.all(RXL[0][1,:,0] == [1,1,2,0,2]))
		self.assertTrue(np.all(RXL[1][0,:,0] == [1,2,0,2,0]))
		self.assertTrue(np.all(RXL[1][1,:,0] == [0,2,1,1,1]))
	def test_rev_comp_init_level(self):
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3, stateful=True)
		func = IS.stateful_chrom_iter
		c, xa_s, ya_s = map(np.vstack, zip(*func('Chr1', seq_len=5, batch_size=2, stranded=True)))
		c, xal, yal = zip(*func('Chr1', seq_len=5, batch_size=2))
		xa = np.vstack(xal)
		ya = np.vstack(yal)
		# Test size
		self.assertEqual(xa_s.shape, (xa.shape[0]*2, xa.shape[1], xa.shape[2]))
		self.assertEqual(ya_s.shape, (ya.shape[0]*2, ya.shape[1], ya.shape[2]))
		# Test X
		xa_s_fab = np.vstack((xa, reader.rev_comp(np.vstack(xal[::-1]))))
		self.assertEqual(xa_s.shape, xa_s_fab.shape)
		self.assertTrue(np.all(xa_s == xa_s_fab))
		# Test Y
		yaf = ya.copy()
		reader.mask(yaf, '-')
		yar = np.flip(np.vstack(yal[::-1]), axis=1)
		reader.mask(yar, '+')
		ya_s_fab = np.vstack((yaf, yar))
		self.assertEqual(ya_s.shape, ya_s_fab.shape)
		self.assertTrue(np.all(ya_s == ya_s_fab))
	def test_is_reverse(self):
		IS = reader.input_slicer(self.fa, self.mr1, stateful=True)
		XL = [x for c,x in IS.stateful_chrom_iter('Chr1', seq_len=5, batch_size=2)]
		#00222202033333021300
		#AATTTTATACCCCCATGCAA
		#11111 22222
		#     11111 22222
		RXL = map(reader.rev_comp, XL)
		for xb, rxb in zip(XL, RXL):
			self.assertFalse(util.is_reverse(xb))
			self.assertTrue(util.is_reverse(rxb))
	def test_mask_notemd(self):
		def encode(arg_list):
			fV, rV = arg_list
			# [G, T]
			# [+G, +T, -G, -T, MD1, MD2]
			n_feat = len(constants.gff3_f2i)
			ret = np.zeros(n_feat)
			findex = {'G':constants.gff3_f2i['+gene'],'T':constants.gff3_f2i['+transposable_element']}
			rindex = {'G':constants.gff3_f2i['-gene'],'T':constants.gff3_f2i['-transposable_element']}
			if fV in findex: ret[findex[fV]] = 1
			if rV in rindex: ret[rindex[rV]] = 1
			return ret
		seq1 = np.array(map(encode, zip('NNTT','GGGG')))
		self.assertEqual(np.sum(seq1), 6)
		self.assertEqual(seq1.shape, (4,66))
		seq2 = np.array(map(encode, zip('NNTT','GTTG')))
		self.assertEqual(np.sum(seq2), 6)
		self.assertEqual(seq2.shape, (4,66))
		batchf = np.stack((seq1, seq2))
		self.assertEqual(batchf.shape, (2,4,66))
		self.assertEqual(batchf.sum(), 12)
		batchr = np.stack((seq1, seq2))
		self.assertEqual(batchr.shape, (2,4,66))
		self.assertEqual(batchr.sum(), 12)
		reader.mask(batchf, '-')
		self.assertEqual(batchf.shape, (2,4,66))
		self.assertEqual(batchf.sum(), 4)
		assert(np.all(batchf == np.stack((map(encode, zip('NNTT','NNNN')), \
						map(encode, zip('NNTT','NNNN'))))))
		reader.mask(batchr, '+')
		self.assertEqual(batchr.shape, (2,4,66))
		self.assertEqual(batchr.sum(), 8)
		assert(np.all(batchr == np.stack((map(encode, zip('NNNN','GGGG')), \
						map(encode, zip('NNNN','GTTG'))))))
	def test_mask_temd(self):
		def encode(arg_list):
			fV, rV, MD1, MD2 = arg_list
			# [G, T]
			# [+G, +T, -G, -T, MD1, MD2]
			n_feat = len(constants.gff3_f2i)+2
			ret = np.zeros(n_feat)
			findex = {'G':constants.gff3_f2i['+gene'],'T':constants.gff3_f2i['+transposable_element']}
			rindex = {'G':constants.gff3_f2i['-gene'],'T':constants.gff3_f2i['-transposable_element']}
			if fV in findex: ret[findex[fV]] = 1
			if rV in rindex: ret[rindex[rV]] = 1
			if 'T' in (fV, rV):
				ret[-2:] = map(int, (MD1, MD2))
			return ret
		seq1 = np.array(map(encode, zip('NNTT','GGGG','0022','0033')))
		self.assertEqual(np.sum(seq1), 6+10)
		self.assertEqual(seq1.shape, (4,68))
		seq2 = np.array(map(encode, zip('NNTT','GTTG','0444','0000')))
		self.assertEqual(np.sum(seq2), 6+12)
		self.assertEqual(seq2.shape, (4,68))
		batchf = np.stack((seq1, seq2))
		self.assertEqual(batchf.shape, (2,4,68))
		self.assertEqual(batchf.sum(), 12+22)
		batchr = np.stack((seq1, seq2))
		self.assertEqual(batchr.shape, (2,4,68))
		self.assertEqual(batchr.sum(), 12+22)
		reader.mask(batchf, '-')
		self.assertEqual(batchf.shape, (2,4,68))
		self.assertEqual(batchf.sum(), 4+18)
		assert(np.all(batchf == np.stack((map(encode, zip('NNTT','NNNN','0022','0033')), \
						map(encode, zip('NNTT','NNNN','0044','0000'))))))
		reader.mask(batchr, '+')
		self.assertEqual(batchr.shape, (2,4,68))
		self.assertEqual(batchr.sum(), 8+8)
		assert(np.all(batchr == np.stack((map(encode, zip('NNNN','GGGG','0000','0000')), \
						map(encode, zip('NNNN','GTTG','0440','0000'))))))
	def test_l2b(self):
		I = reader.input_slicer(self.fa, self.mr1)
		iA = np.tile(np.arange(9).reshape((9,1)), (1,2))
		o_str = np.array(I._list2batch_str(iA, 5, 3, 2))
		self.assertEqual(o_str.shape, (3,5,2))
		for i in range(3):
			expected = np.tile(np.arange(i*2,i*2+5).reshape((5,1)), (1,2))
			if not np.all(o_str[i] == expected):
				print "L i=%i   %s != %s"%(i, o_str[i], expected)
			self.assertTrue(np.all(o_str[i] == expected))
		o_num = I._list2batch_num(iA, 5, 3, 2)
		self.assertEqual(o_num.shape, (3,5,2))
		for i in range(3):
			expected = np.tile(np.arange(i*2,i*2+5).reshape((5,1)), (1,2))
			if not np.all(o_num[i] == expected):
				print "N i=%i   %s != %s"%(i, o_num[i], expected)
			self.assertTrue(np.all(o_num[i] == expected))
		o_coord = I._coord2batch(('Chr1', 0, 9), 5, 3, 2)
		self.assertEqual(o_coord, [('Chr1', 0, 5), ('Chr1', 2, 7), ('Chr1', 4, 9)])
	def test_chrom_iter(self):
		I = reader.input_slicer(self.fa, self.mr1)
		chrom_len = 20
		for seq_len in (5,6):
			for offset in (1,2):
				for batch_size in (1,2):
					for hvd_size in (1,2):
						for hvd_rank in range(0,hvd_size):
							#print "seq_len: %i   offset: %i   batch_size: %i   hvd_size: %i   hvd_rank: %i"%(seq_len, offset, batch_size, hvd_size, hvd_rank)
							CL = [c for c,x in I.chrom_iter('Chr1', seq_len, \
								offset, batch_size, hvd_rank, hvd_size)]
							#print CL
							full_len = seq_len+(batch_size-1)*offset
							EL = [I._coord2batch(('Chr1',i,min(i+seq_len+(batch_size-1)*offset,chrom_len)), seq_len, batch_size, offset) for i \
								in range(hvd_rank*(offset*batch_size), \
									chrom_len-full_len+1, \
									hvd_size*offset*batch_size)]
							max_len = find_max(seq_len, offset, batch_size, hvd_size)
							EL += EL[:max_len-len(EL)]
							#print EL
							self.assertEqual(CL, EL)
	def test_genome_iter(self):
		I = reader.input_slicer(self.fa, self.mr1)
		chrom_len = 20
		for seq_len in (5,6):
			for offset in (1,3):
				for batch_size in (1,2):
					for hvd_size in (1,2):
						for hvd_rank in range(0,hvd_size):
							CL = [c for c,x in I.genome_iter(seq_len, \
								offset, batch_size, hvd_rank, hvd_size)]
							#print "seq_len: %i   offset: %i   batch_size: %i   hvd_size: %i   hvd_rank: %i"%(seq_len, offset, batch_size, hvd_size, hvd_rank)
							#print CL
							full_len = seq_len+(batch_size-1)*offset
							s = hvd_rank*(offset*batch_size)
							e = chrom_len-full_len+1
							o = hvd_size*offset*batch_size
							C1L = [I._coord2batch(('Chr1',i,min(i+seq_len+(batch_size-1)*offset,chrom_len)), seq_len, batch_size, offset) for i \
								in range(s, e, o)]
							C2L = [I._coord2batch(('Chr2',i,min(i+seq_len+(batch_size-1)*offset,chrom_len)), seq_len, batch_size, offset) for i \
								in range(s, e, o)]
							#print C1L
							#print C2L
							max_len = find_max(seq_len, offset, batch_size, hvd_size)
							if find_max(seq_len, offset, batch_size, hvd_size) != len(C1L):
								C1L = C1L+C1L[:max_len-len(C1L)]
								C2L = C2L+C2L[:max_len-len(C2L)]
							#print C1L+C2L
							self.assertEqual(CL, C1L+C2L)
	def test_stateful_chrom_iter(self):
		IS = reader.input_slicer(self.fa, self.mr1, stateful=True)
		I = reader.input_slicer(self.fa, self.mr1)
		chrom_len = 20
		chrom_quality = I.RC.chrom_qualities['Chr1']
		for seq_len in (2,5):
			for batch_size in (1,3,4):
				for hvd_size in (1,2):
					for hvd_rank in range(0,hvd_size):
						size, rank = hvd_size, hvd_rank
						#print "seq_len: %i   batch_size: %i   hvd_size: %i   hvd_rank: %i"%(seq_len, batch_size, hvd_size, hvd_rank)
						contigs_per_rank = int(batch_size/hvd_size)
						if not contigs_per_rank and hvd_size > 1:
							rank, size = 0, 1
							contigs_per_rank = batch_size
						max_contig_len = (2*chrom_len)/(batch_size+1)
						n_batches = int(max_contig_len/seq_len)
						starts = np.arange(batch_size)*(max_contig_len/2)
						ends = starts+max_contig_len
						EL = []
						for iB in range(n_batches):
							c = []
							for iS in range(contigs_per_rank*rank, contigs_per_rank*(rank+1)):
								region_start = starts[iS]+iB*seq_len
								c.append(I._get_region('Chr1', region_start, chrom_len, chrom_quality, seq_len)[0])
							EL.append(c)
						CL = [c for c,x in IS.stateful_chrom_iter('Chr1', seq_len, \
							1, batch_size, hvd_rank, hvd_size)]
						#print CL
						#print EL
						for c,x in IS.stateful_chrom_iter('Chr1', seq_len, 1, batch_size, hvd_rank, hvd_size):
							self.assertEqual(x.shape, (contigs_per_rank, seq_len, 10))
						self.assertEqual(CL, EL)
					if hvd_size == 2:
						R1 = [c for c,x in IS.stateful_chrom_iter('Chr1', seq_len, \
							1, batch_size, 0, hvd_size)]
						R2 = [c for c,x in IS.stateful_chrom_iter('Chr1', seq_len, \
							1, batch_size, 1, hvd_size)]
						self.assertEqual(len(R1), len(R2))
	def test_stranded_stateful_chrom_iter(self):
		IS = reader.input_slicer(self.fa, self.mr1, stateful=True)
		I = reader.input_slicer(self.fa, self.mr1)
		chrom_len = 20
		chrom_quality = I.RC.chrom_qualities['Chr1']
		for seq_len in (2,5):
			for batch_size in (1,3,4):
				for hvd_size in (1,2):
					for hvd_rank in range(0,hvd_size):
						size, rank = hvd_size, hvd_rank
						#print "seq_len: %i   batch_size: %i   hvd_size: %i   hvd_rank: %i"%(seq_len, batch_size, hvd_size, hvd_rank)
						contigs_per_rank = int(batch_size/hvd_size)
						if not contigs_per_rank and hvd_size > 1:
							rank, size = 0, 1
							contigs_per_rank = batch_size
						max_contig_len = (2*chrom_len)/(batch_size+1)
						n_batches = int(max_contig_len/seq_len)
						starts = np.arange(batch_size)*(max_contig_len/2)
						ends = starts+max_contig_len
						EL = []
						for iB in range(n_batches):
							c = []
							for iS in range(contigs_per_rank*rank, contigs_per_rank*(rank+1)):
								region_start = starts[iS]+iB*seq_len
								c.append(I._get_region('Chr1', region_start, chrom_len, chrom_quality, seq_len)[0])
							EL.append(c)
						CL = [c for c,x in IS.stateful_chrom_iter('Chr1', seq_len, \
							1, batch_size, hvd_rank, hvd_size, stranded=True)]
						#print CL
						#print EL
						for c,x in IS.stateful_chrom_iter('Chr1', seq_len, 1, batch_size, hvd_rank, hvd_size, stranded=True):
							self.assertEqual(x.shape, (contigs_per_rank, seq_len, 10))
						self.assertEqual(CL, EL+EL[::-1])
					if hvd_size == 2:
						R1 = [c for c,x in IS.stateful_chrom_iter('Chr1', seq_len, \
							1, batch_size, 0, hvd_size)]
						R2 = [c for c,x in IS.stateful_chrom_iter('Chr1', seq_len, \
							1, batch_size, 1, hvd_size)]
						self.assertEqual(len(R1), len(R2))
	def test_input_iter(self):
		I = reader.input_slicer(self.fa, self.mr1)
		IL = list(I.genome_iter())
		#for c, x in IL:
		#	print(''.join(map(lambda i: constants.index2base[i[0]], x)))
		self.assertTrue(np.allclose(IL[0][1][0][0], [constants.base2index['A'], 1.0/20, 0,0,0,0,0,0, 2,3]))
		self.assertTrue(np.allclose(IL[9][1][0][0], [constants.base2index['C'], 10.0/20, 0,0,0,0,10.0/20,20, 2,3]))
		self.assertEqual(len(IL), 16+16)
	def test_input_iter_10(self):
		I = reader.input_slicer(self.fa, self.mr1)
		IL = list(I.genome_iter(seq_len=10))
		self.assertEqual(len(IL), 11+11)
	def test_gff2interval(self):
		GI = reader.gff3_interval(self.gff3, force=True)
		res1 = GI.interval_tree['Chr1'].search(0,2)
		self.assertEqual(len(res1), 2)
		res2 = GI.interval_tree['Chr1'].search(0,3)
		self.assertEqual(len(res2), 3)
	def test_gff2array(self):
		###gff-version   3
		#Chr1    test    CDS     3       10      .       +       .       ID=team_0
		#Chr1    test    gene    3       10      .       +       .       ID=team_1
		#Chr1    test    exon    4       7       .       +       .       ID=team_2
		#Chr1    test    transposable_element    10      15      .       -       .       ID=team_3;Order=LTR;Superfamily=Gypsy
		#Chr2    test    CDS     2       15      .       -       .       ID=team_4
		#Chr2    test    gene    2       15      .       -       .       ID=team_5
		#Chr2    test    exon    3       7       .       -       .       ID=team_6
		#Chr2    test    exon    9       14      .       -       .       ID=team_7
		GI = reader.gff3_interval(self.gff3, force=True)
		res1 = GI.fetch('Chr1', 0, 15)
		tmp = np.zeros((15, self.n_outputs), dtype=np.uint8)
		tmp[2:10,constants.gff3_f2i['+CDS']] = 1
		tmp[2:10,constants.gff3_f2i['+gene']] = 1
		tmp[3:7,constants.gff3_f2i['+exon']] = 1
		tmp[9:15,constants.gff3_f2i['-transposable_element']] = 1
		tmp[9:15,len(constants.gff3_f2i)] = constants.te_order_f2i['ltr']
		tmp[9:15,len(constants.gff3_f2i)+1] = constants.te_sufam_f2i['gypsy']
		self.assertEqual(res1.shape, tmp.shape)
		for i in range(15):
			if not np.array_equal(res1[i], tmp[i]):
				print("At index %i"%(i))
				print("Code:",res1[i])
				print("Test:",tmp[i])
		self.assertTrue(np.array_equal(res1, tmp))
		res2 = GI.fetch('Chr2', 0, 18)
		tmp = np.zeros((18,self.n_outputs))
		tmp[1:15,constants.gff3_f2i['-CDS']] = 1
		tmp[1:15,constants.gff3_f2i['-gene']] = 1
		tmp[2:7,constants.gff3_f2i['-exon']] = 1
		tmp[8:14,constants.gff3_f2i['-exon']] = 1
		self.assertTrue(np.array_equal(res2, tmp))
	def test_input_iter_gff3(self):
		I = reader.input_slicer(self.fa, self.mr1, self.gff3)
		XYL = list(I.genome_iter()) 
		self.assertEqual(len(XYL), (20-5+1)*2)
		self.assertTrue(np.allclose(XYL[0][1][0][0], [constants.base2index['A'], 1.0/20, 0,0,0,0,0,0, 2,3]))
		self.assertTrue(np.allclose(XYL[9][1][0][0], [constants.base2index['C'], 10.0/20, 0,0,0,0,10.0/20,20, 2,3]))
		self.assertTrue(XYL[9][2][0][0][constants.gff3_f2i['+CDS']], 1)
		for i in range(9, 15):
			self.assertEqual(XYL[i][2][0][0][constants.gff3_f2i['-transposable_element']], 1)
			self.assertEqual(XYL[i][2][0][0][len(constants.gff3_f2i)], 3)
			self.assertEqual(XYL[i][2][0][0][len(constants.gff3_f2i)+1], 7)
		self.assertEqual(sum(XYL[0][2][0][0]), 0)
		self.assertEqual(sum(XYL[1][2][0][0]), 0)
		self.assertEqual(sum(XYL[16][2][0][0]), 0)
		self.assertEqual(sum(XYL[8][2][0][2]), 11)
		self.assertEqual(sum(XYL[8][2][0][1]), 13)
		self.assertEqual(len(XYL), 16+16)
	def test_mse_interval_midpoint(self):
		MI = writer.MSE_interval(self.fa, 'mse_tmp', 0)
		x,y = MI._to_midpoint(0, 10, 0.5)
		self.assertEqual(x, [5])
		self.assertEqual(y, [0.5])
	def test_mse_interval_range(self):
		MI = writer.MSE_interval(self.fa, 'mse_tmp', 0)
		x,y = MI._to_range(0, 10, 0.5)
		self.assertEqual(x, [0,10])
		self.assertEqual(y, [0.5,0.5])
	def test_mse_interval_single(self):
		MI = writer.MSE_interval(self.fa, 'mse_tmp', 0)
		MI.add_batch([('Chr1',0,10),('Chr1',10,20)], 0.5)
		MI.add_batch([('Chr1',5,15),('Chr2',0,5)], 1.0)
		for n,m in (('mean',np.mean), ('median',np.median), ('sum',np.sum)):
			self.assertEqual(MI._region_to_agg_value('Chr1',0,5,n), m([0.5]*5))
			self.assertEqual(MI._region_to_agg_value('Chr1',0,10,n), m([0.5]*10+[1.0]*5))
			self.assertEqual(MI._region_to_agg_value('Chr1',0,15,n), m([0.5]*15+[1.0]*10))
			self.assertEqual(MI._region_to_agg_value('Chr1',0,20,n), m([0.5]*20+[1.0]*10))
			self.assertEqual(MI._region_to_agg_value('Chr3',0,5,n), -1)
			self.assertEqual(MI._region_to_agg_value('Chr2',0,10,n), m([1.0]*5))
			self.assertEqual(MI._region_to_xy('Chr1',0,5,n), ([2.5], [m([0.5]*5)]))
			self.assertEqual(MI._region_to_xy('Chr1',0,10,n), ([5], [m([0.5]*10+[1.0]*5)]))
			self.assertEqual(MI._region_to_xy('Chr1',0,15,n), ([7.5], [m([0.5]*15+[1.0]*10)]))
			self.assertEqual(MI._region_to_xy('Chr1',0,20,n), ([10], [m([0.5]*20+[1.0]*10)]))
			self.assertEqual(MI._region_to_xy('Chr3',0,5,n), ([2.5], [-1]))
			self.assertEqual(MI._region_to_xy('Chr2',0,10,n), ([5], [m([1.0]*5)]))
			x, y = MI.to_array('Chr1',width=5, method=n)
			self.assertEqual(x, [2.5, 7.5, 12.5, 17.5])
			self.assertEqual(y, map(m, [[0.5]*5, [0.5]*5+[1.0]*5, [0.5]*5+[1.0]*5, [0.5]*5]))
			x, y = MI.to_array('Chr2',width=5, method=n)
			self.assertEqual(x, [2.5, 7.5, 12.5, 17.5])
			self.assertEqual(y, map(m, [[1.0]*5, [-1]*1, [-1]*1, [-1]*1]))
	def test_mse_interval_distrib(self):
		MI0 = writer.MSE_interval(self.fa, 'mse_tmp', 0)
		MI1 = writer.MSE_interval(self.fa, 'mse_tmp', 1)
		MI0.add_batch([('Chr1',0,10),('Chr1',10,20)], 0.5)
		MI1.add_batch([('Chr1',5,15),('Chr2',0,5)], 1.0)
		MI0.dump()
		MI1.dump()
		MI0.load_all()
		MI1.load_all()
		for n,m in (('mean',np.mean), ('median',np.median), ('sum',np.sum)):
			self.assertEqual(MI0._region_to_agg_value('Chr1',0,5,n), m([0.5]*5))
			self.assertEqual(MI0._region_to_agg_value('Chr1',0,10,n), m([0.5]*10+[1.0]*5))
			self.assertEqual(MI0._region_to_agg_value('Chr1',0,15,n), m([0.5]*15+[1.0]*10))
			self.assertEqual(MI0._region_to_agg_value('Chr1',0,20,n), m([0.5]*20+[1.0]*10))
			self.assertEqual(MI0._region_to_agg_value('Chr3',0,5,n), -1)
			self.assertEqual(MI0._region_to_agg_value('Chr2',0,10,n), m([1.0]*5))
			self.assertEqual(MI0._region_to_xy('Chr1',0,5,n), ([2.5], [m([0.5]*5)]))
			self.assertEqual(MI0._region_to_xy('Chr1',0,10,n), ([5], [m([0.5]*10+[1.0]*5)]))
			self.assertEqual(MI0._region_to_xy('Chr1',0,15,n), ([7.5], [m([0.5]*15+[1.0]*10)]))
			self.assertEqual(MI0._region_to_xy('Chr1',0,20,n), ([10], [m([0.5]*20+[1.0]*10)]))
			self.assertEqual(MI0._region_to_xy('Chr3',0,5,n), ([2.5], [-1]))
			self.assertEqual(MI0._region_to_xy('Chr2',0,10,n), ([5], [m([1.0]*5)]))
			x, y = MI0.to_array('Chr1',width=5, method=n)
			self.assertEqual(x, [2.5, 7.5, 12.5, 17.5])
			self.assertEqual(y, map(m, [[0.5]*5, [0.5]*5+[1.0]*5, [0.5]*5+[1.0]*5, [0.5]*5]))
			x, y = MI0.to_array('Chr2',width=5, method=n)
			self.assertEqual(x, [2.5, 7.5, 12.5, 17.5])
			self.assertEqual(y, map(m, [[1.0]*5, [-1]*1, [-1]*1, [-1]*1]))
	def test_mse_array_single(self):
		MI = writer.MSE_interval(self.fa, 'mse_tmp', 0)
		MI.add_predict_batch([('Chr1',0,10),('Chr1',10,20)], \
			np.array([[[0,1],[1,0]], [[0,1],[1,0]]]), \
			np.array([[[1,0],[1,0]], [[1,0],[1,0]]])) # 0.5
		MI.add_predict_batch([('Chr1',5,15),('Chr2',0,5)], \
			np.array([[[0,1],[1,0]], [[0,1],[1,0]]]), \
			np.array([[[1,0],[0,1]], [[1,0],[0,1]]])) # 1.0
		for n,m in (('mean',np.mean), ('sum',np.sum)):
			self.assertEqual(MI._region_to_agg_value('Chr1',0,5,n), m([0.5]*5))
			self.assertEqual(MI._region_to_agg_value('Chr1',0,10,n), m([0.5]*10+[1.0]*5))
			self.assertEqual(MI._region_to_agg_value('Chr1',0,15,n), m([0.5]*15+[1.0]*10))
			self.assertEqual(MI._region_to_agg_value('Chr1',0,20,n), m([0.5]*20+[1.0]*10))
			self.assertEqual(MI._region_to_agg_value('Chr3',0,5,n), -1)
			self.assertEqual(MI._region_to_agg_value('Chr2',0,10,n), m([1.0]*5))
			self.assertEqual(MI._region_to_xy('Chr1',0,5,n), ([2.5], [m([0.5]*5)]))
			self.assertEqual(MI._region_to_xy('Chr1',0,10,n), ([5], [m([0.5]*10+[1.0]*5)]))
			self.assertEqual(MI._region_to_xy('Chr1',0,15,n), ([7.5], [m([0.5]*15+[1.0]*10)]))
			self.assertEqual(MI._region_to_xy('Chr1',0,20,n), ([10], [m([0.5]*20+[1.0]*10)]))
			self.assertEqual(MI._region_to_xy('Chr3',0,5,n), ([2.5], [-1]))
			self.assertEqual(MI._region_to_xy('Chr2',0,10,n), ([5], [m([1.0]*5)]))
			x, y = MI.to_array('Chr1',width=5, method=n)
			self.assertEqual(x, [2.5, 7.5, 12.5, 17.5])
			self.assertEqual(y, map(m, [[0.5]*5, [0.5]*5+[1.0]*5, [0.5]*5+[1.0]*5, [0.5]*5]))
			x, y = MI.to_array('Chr2',width=5, method=n)
			self.assertEqual(x, [2.5, 7.5, 12.5, 17.5])
			self.assertEqual(y, map(m, [[1.0]*5, [-1]*1, [-1]*1, [-1]*1]))
	def test_mse_array_distrib(self):
		MI0 = writer.MSE_interval(self.fa, 'mse_tmp', 0)
		MI1 = writer.MSE_interval(self.fa, 'mse_tmp', 1)
		MI0.add_predict_batch([('Chr1',0,10),('Chr1',10,20)], \
			np.array([[[0,1],[1,0]], [[0,1],[1,0]]]), \
			np.array([[[1,0],[1,0]], [[1,0],[1,0]]])) # 0.5
		MI1.add_predict_batch([('Chr1',5,15),('Chr2',0,5)], \
			np.array([[[0,1],[1,0]], [[0,1],[1,0]]]), \
			np.array([[[1,0],[0,1]], [[1,0],[0,1]]])) # 1.0
		MI0.dump()
		MI1.dump()
		MI0.load_all()
		MI1.load_all()
		for n,m in (('mean',np.mean), ('sum',np.sum)):
			self.assertEqual(MI0._region_to_agg_value('Chr1',0,5,n), m([0.5]*5))
			self.assertEqual(MI0._region_to_agg_value('Chr1',0,10,n), m([0.5]*10+[1.0]*5))
			self.assertEqual(MI0._region_to_agg_value('Chr1',0,15,n), m([0.5]*15+[1.0]*10))
			self.assertEqual(MI0._region_to_agg_value('Chr1',0,20,n), m([0.5]*20+[1.0]*10))
			self.assertEqual(MI0._region_to_agg_value('Chr3',0,5,n), -1)
			self.assertEqual(MI0._region_to_agg_value('Chr2',0,10,n), m([1.0]*5))
			self.assertEqual(MI0._region_to_xy('Chr1',0,5,n), ([2.5], [m([0.5]*5)]))
			self.assertEqual(MI0._region_to_xy('Chr1',0,10,n), ([5], [m([0.5]*10+[1.0]*5)]))
			self.assertEqual(MI0._region_to_xy('Chr1',0,15,n), ([7.5], [m([0.5]*15+[1.0]*10)]))
			self.assertEqual(MI0._region_to_xy('Chr1',0,20,n), ([10], [m([0.5]*20+[1.0]*10)]))
			self.assertEqual(MI0._region_to_xy('Chr3',0,5,n), ([2.5], [-1]))
			self.assertEqual(MI0._region_to_xy('Chr2',0,10,n), ([5], [m([1.0]*5)]))
			x, y = MI0.to_array('Chr1',width=5, method=n)
			self.assertEqual(x, [2.5, 7.5, 12.5, 17.5])
			self.assertEqual(y, map(m, [[0.5]*5, [0.5]*5+[1.0]*5, [0.5]*5+[1.0]*5, [0.5]*5]))
			x, y = MI0.to_array('Chr2',width=5, method=n)
			self.assertEqual(x, [2.5, 7.5, 12.5, 17.5])
			self.assertEqual(y, map(m, [[1.0]*5, [-1]*1, [-1]*1, [-1]*1]))
	def test_vote(self):
		from functools import reduce
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3)
		OA = writer.output_aggregator(self.fa)
		for chrom in sorted(IS.FA.references):
			for cb,xb,yb in IS.chrom_iter(chrom, seq_len=5, offset=2, batch_size=2):
				for c,y in zip(cb, yb):
					OA.vote(*c, array=y)
		out_lines = OA.write_gff3()
		self._compare_against_file(out_lines, self.gff3)
	def test_vote_stateful(self):
		from functools import reduce
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3)
		OA = writer.output_aggregator(self.fa)
		for chrom in sorted(IS.FA.references):
			for cb,xb,yb in IS.chrom_iter(chrom, seq_len=5, offset=2, batch_size=2):
				for c,y in zip(cb, yb):
					OA.vote(*c, array=y, overwrite=True)
		out_lines = OA.write_gff3()
		self._compare_against_file(out_lines, self.gff3)
	def test_batch_new(self):
		IS = reader.input_slicer(self.fa, self.mr1)
		BL = list(IS.genome_iter(seq_len=5, batch_size=4))
		#for c,x in BL: print c
		self.assertEqual(len(BL), 4+4)
		for out in BL:
			self.assertEqual(len(out), 3 if IS.gff3_file else 2)
			self.assertEqual(np.array(out[1]).shape, (4, 5, 10))
			if IS.gff3_file:
				self.assertEqual(np.array(out[2]).shape, (4, 5, self.n_outputs))
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3)
		BL = list(IS.genome_iter(seq_len=5, batch_size=4))
		self.assertEqual(len(BL), 4+4)
		for out in BL:
			self.assertEqual(len(out), 3 if IS.gff3_file else 2)
			self.assertEqual(np.array(out[1]).shape, (4, 5, 10))
			if IS.gff3_file:
				self.assertEqual(np.array(out[2]).shape, (4, 5, self.n_outputs))
	def test_batch_stateful(self):
		IS = reader.input_slicer(self.fa, self.mr1, stateful=True)
		I = reader.input_slicer(self.fa, self.mr1)
		BL = list(IS.stateful_chrom_iter(chrom='Chr1', seq_len=5, batch_size=2))
		#for c,x in BL:
		#	print c
		#	for seq in x: print seq
		self.assertEqual(len(BL), 2)
		for out in BL:
			self.assertEqual(len(out), 3 if IS.gff3_file else 2)
			if IS.gff3_file:
				c,x,y = out
			else:
				c,x = out
			self.assertEqual(np.array(x).shape, (2, 5, 10))
			for i, region in enumerate(c):
				c_region, x_region = I._get_region(region[0], region[1], 20, 3, 5)
				self.assertTrue(np.all(x_region == x[i]))
			if IS.gff3_file:
				self.assertEqual(np.array(out[2]).shape, (4, 5, len(constants.gff3_f2i)))
	def test_batch_stateful_stranded(self):
		IS = reader.input_slicer(self.fa, self.mr1, stateful=True)
		I = reader.input_slicer(self.fa, self.mr1)
		BL = list(IS.stateful_chrom_iter(chrom='Chr1', seq_len=5, batch_size=2, stranded=True))
		self.assertEqual(len(BL), 2*2)
		for out in BL:
			self.assertEqual(len(out), 3 if IS.gff3_file else 2)
			if IS.gff3_file: c,x,y = out
			else: c,x = out
			self.assertEqual(np.array(x).shape, (2, 5, 10))
			for i, region in enumerate(c):
				c_region, x_region = I._get_region(region[0], region[1], 20, 3, 5)
				if util.is_reverse(x):
					self.assertTrue(np.all(reader.rev_comp(x_region.reshape(1,5,10))[0] == x[i]))
				else:
					self.assertTrue(np.all(x_region == x[i]))
			if IS.gff3_file:
				self.assertEqual(np.array(out[2]).shape, (4, 5, len(constants.gff3_f2i)))
	def test_batch_10(self):
		IS = reader.input_slicer(self.fa, self.mr1)
		BL = list(IS.genome_iter(seq_len=10, batch_size=11))
		self.assertEqual(len(BL), np.round(22/11))
		for out in BL:
			self.assertEqual(len(out), 2)
			self.assertEqual(np.array(out[1]).shape, (11, 10, 10))
	def test_batch_uneven(self):
		IS = reader.input_slicer(self.fa, self.mr1)
		BL = list(IS.genome_iter(batch_size=5))
		#for c,x in BL: print c
		self.assertEqual(len(BL), 6)
		for out in BL:
			self.assertEqual(len(out), 2)
			self.assertEqual(np.array(out[1]).shape, (5, 5, 10))
	def test_same_model(self):
		if not self.test_model: return
		seq_len = 5
		mse_lists = [[], []]
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3)
		for i in range(2):
			name = 'model_%i'%(i)
			m = model.sleight_model(name, self.n_inputs, seq_len, self.n_outputs, \
				n_neurons=20, n_layers=1, learning_rate=0.001, dropout=0, \
				cell_type='rnn', reg_kernel=False, reg_bias=False, reg_activity=False, \
				l1=0, l2=0, bidirectional=False, merge_mode='concat', \
				stateful=False, hidden_list=[], save_dir='.')
			for epoch in range(2):
				for cb, xb, yb in IS.genome_iter(seq_len, batch_size=20-seq_len+1):
					mse, acc, time = m.train(xb, yb)
					mse_lists[i].append(mse)
			del m
		self.assertEqual(mse_lists[0], mse_lists[1])
	def test_model_effect(self):
		def train_mse(IS, m, epoch=3):
			seq_len, out_mse = 5, []
			for e in range(epoch):
				out_mse += [m.train(x,y)[0] for c,x,y in IS.genome_iter(seq_len, batch_size=20-seq_len+1)]
			return out_mse
		if not self.test_model: return
		seq_len = 5
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3)
		constant_args = (self.n_inputs, seq_len, self.n_outputs)
		# Baseline
		baseline_mse = train_mse(IS,  model.sleight_model('default', *constant_args))
		#print baseline_mse
		# Variations
		neurons_mse = train_mse(IS,  model.sleight_model('neurons', *constant_args, n_neurons=100))
		layers_mse = train_mse(IS,  model.sleight_model('layers', *constant_args, n_layers=3))
		learning_mse = train_mse(IS,  model.sleight_model('learning', *constant_args, learning_rate=0.01))
		dropout_mse = train_mse(IS,  model.sleight_model('dropout', *constant_args, dropout=0.10))
		lstm_mse = train_mse(IS,  model.sleight_model('lstm', *constant_args, cell_type='lstm'))
		bidirectional_mse = train_mse(IS,  model.sleight_model('bidirectional', *constant_args, bidirectional=True))
		bias_mse = train_mse(IS,  model.sleight_model('bias', *constant_args, reg_bias=True, l1=0.1))
		hidden_mse = train_mse(IS,  model.sleight_model('hidden', *constant_args, hidden_list=[50]))
		baseline2_mse = train_mse(IS,  model.sleight_model('default', *constant_args))
		# Check baseline
		self.assertEqual(len(baseline_mse), len(baseline2_mse))
		self.assertTrue(baseline_mse == baseline2_mse)
		# Make sure different parameters had an effect
		for mse_list in (neurons_mse, layers_mse, dropout_mse, lstm_mse, bidirectional_mse, hidden_mse):
			#print mse_list
			self.assertEqual(len(baseline_mse), len(mse_list))
			for i in range(len(mse_list)):
				self.assertNotEqual(baseline_mse[i], mse_list[i])
		for mse_list in (learning_mse, bias_mse):
			#print mse_list
			self.assertEqual(len(baseline_mse), len(mse_list))
			self.assertEqual(baseline_mse[0], mse_list[0])
			for i in range(1,len(mse_list)):
				self.assertNotEqual(baseline_mse[i], mse_list[i])
	def test_train_01(self):
		if os.path.exists('test_model'): rmtree('test_model')
		def a2s(a):
			return '['+', '.join(map(lambda x: '%.2f'%(x), a))+']'
		if not self.test_model: return
		batch_size = 20-self.seq_len+1
		# create models
		M = model.sleight_model('default', self.n_inputs, self.seq_len, self.n_outputs, \
			n_neurons=256, learning_rate=0.01, bidirectional=True, \
			save_dir='test_model', cell_type='lstm')
		# train models
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3)
		ISBL = list(IS.genome_iter(self.seq_len, batch_size=batch_size))
		#for c,x,y in ISBL:
		#	for i in range(len(c)):
		#		chrom, start, end = c[i]
		#		print c[i]
		#		for j,k in enumerate(range(start, end)):
		#			print "%s %2i %s"%(chrom, k, a2s(x[i][j])), np.nonzero(y[i][j])[0]
		#M.model.fit(x_batch, y_batch, batch_size=batch_size, epochs=50, shuffle=True)
		for epoch in range(1,self.n_epoch+1):
			for c,x,y in ISBL:
				mse,acc,time = M.train(x,y)
		M.save()
		self.assertTrue(len(glob('%s*'%(M.save_file))) > 0)
		# Vote
		OA = writer.output_aggregator(self.fa)
		for c, xb, yb in IS.genome_iter(self.seq_len, batch_size=1):
			y = yb[0]
			y_pred = M.predict(xb)[0]
			for feature_index in range(len(y[0])):
				ys = ', '.join([str(y[bi][feature_index]) for bi in range(len(y))])
				yps = ', '.join([str(y_pred[bi][feature_index]) for bi in range(len(y))])
				if ys != yps:
					print("%s:%i-%i FI:%2i Y=[%s]  Y_PRED=[%s]"%(c[0][0], c[0][1], c[0][2], feature_index, ys, yps))
			self.assertTrue(np.array_equal(y, y_pred))
			OA.vote(*c[0], array=y_pred)
		# Compare
		out_lines = OA.write_gff3()
		self._compare_against_file(out_lines, self.gff3)
	def test_train_02(self):
		if not self.test_model: return
		batch_size = 20-self.seq_len+1
		# create models
		M = model.sleight_model('default', self.n_inputs, self.seq_len, self.n_outputs, \
			n_neurons=256, learning_rate=0.01, bidirectional=True, \
			save_dir='test_model', cell_type='lstm')
		self.assertTrue(len(glob('%s*'%(M.save_file))) > 0)
		M.restore()
		# Vote
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3)
		OA = writer.output_aggregator(self.fa)
		for c, xb, yb in IS.genome_iter(self.seq_len, batch_size=1):
			y = yb[0]
			y_pred = M.predict(xb)[0]
			for feature_index in range(len(y[0])):
				ys = ', '.join([str(y[bi][feature_index]) for bi in range(len(y))])
				yps = ', '.join([str(y_pred[bi][feature_index]) for bi in range(len(y))])
				if ys != yps:
					print("%s:%i-%i FI:%2i Y=[%s]  Y_PRED=[%s]"%(c[0][0], c[0][1], c[0][2], feature_index, ys, yps))
			self.assertTrue(np.array_equal(y, y_pred))
			OA.vote(*c[0], array=y_pred)
		# Compare
		out_lines = OA.write_gff3()
		self._compare_against_file(out_lines, self.gff3)
		if os.path.exists(M.save_dir):
			rmtree(M.save_dir)
	def test_bi_stateful(self):
		if not self.test_model: return
		seq_len, batch_size = 20, 1
		# create models
		M = model.sleight_model('default', self.n_inputs, seq_len, self.n_outputs, n_neurons=48, \
			learning_rate=0.01, bidirectional=True, save_dir='test_model', \
			cell_type='lstm', stateful=batch_size)
		# train models
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3, stateful=True)
		for epoch in range(1,100+1):
			for chrom in sorted(IS.FA.references):
				M.model.reset_states()
				for cb, xb, yb in IS.stateful_chrom_iter(chrom, seq_len=seq_len, offset=1, batch_size=batch_size):
					mse,acc,time = M.train(xb,yb)
				M.model.reset_states()
		M.save()
		self.assertTrue(len(glob('%s*'%(M.save_file))) > 0)
		# Vote
		OA = writer.output_aggregator(self.fa)
		for chrom in sorted(IS.FA.references):
			M.model.reset_states()
			batch_index = 0
			for cb, xb, yb in IS.stateful_chrom_iter(chrom, seq_len=seq_len, offset=1, batch_size=batch_size):
				#print cb
				y_pred_batch = M.predict(xb)
				#for fi in range(self.n_outputs):
				#	fn = constants.gff3_i2f[fi] if fi < len(constants.gff3_i2f) else "TE_O_SUFAM"
				#	for bi in range(batch_size):
				#		yv = yb[bi,:,fi]
				#		ypv = y_pred_batch[bi,:,fi]
				#		if np.any(yv != ypv):
				#			cc, cs, ce = cb[bi]
				#			print "%s:%02i-%02i %25s %s %s"%(cc, cs, ce, fn, yv, ypv)
				for c, x, y, yp in zip(cb, xb, yb, y_pred_batch):
					OA.vote(*c, array=yp, overwrite=True)
				batch_index += 1
			M.model.reset_states()
		# Compare
		out_lines = OA.write_gff3()
		self._compare_against_file(out_lines, self.gff3)
		if os.path.exists(M.save_dir):
			rmtree(M.save_dir)
	def test_train_stateful_01(self):
		def a2s(a):
			return '['+', '.join(map(lambda x: '%.2f'%(x), a))+']'
		def non_zero(a):
			return ' '.join(["%i:%i"%(i,a[i]) for i in np.nonzero(a)[0]])
		if not self.test_model: return
		seq_len, batch_size = 4, 4
		# create models
		M = model.sleight_model('default', self.n_inputs, seq_len, self.n_outputs, n_neurons=80, \
			learning_rate=0.01, bidirectional=False, save_dir='test_model', \
			cell_type='lstm', stateful=batch_size)
		# train models
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3, stateful=True)
		for epoch in range(1,100+1):
			M.model.reset_states()
			for chrom in sorted(IS.FA.references):
				for cb, xb, yb in IS.stateful_chrom_iter(chrom, seq_len, 1, batch_size):
					mse,acc,time = M.train(xb,yb)
				M.model.reset_states()
			#print epoch, mse
		M.save()
		self.assertTrue(len(glob('%s*'%(M.save_file))) > 0)
		# Vote
		OA = writer.output_aggregator(self.fa)
		for chrom in sorted(IS.FA.references):
			M.model.reset_states()
			for cb, xb, yb in IS.stateful_chrom_iter(chrom, seq_len, 1, batch_size=4):
				#xb = np.tile(xb, (batch_size,1,1))
				#print xb.shape
				y_pred_batch = M.predict(xb)
				for c, x, y, yp in zip(cb, xb, yb, y_pred_batch):
					#for feature_index in range(len(y[0])):
					#	ys = ', '.join([str(y[bi][feature_index]) for bi in range(len(y))])
					#	yps = ', '.join([str(yp[bi][feature_index]) for bi in range(len(y))])
					#	if ys != yps:
					#		print("%s:%i-%i FI:%2i Y=[%s]  Y_PRED=[%s]"%(c[0], c[1], c[2], feature_index, ys, yps))
					#self.assertTrue(np.array_equal(y, yp))
					OA.vote(*c, array=yp, overwrite=True)
		# Compare
		out_lines = OA.write_gff3()
		self._compare_against_file(out_lines, self.gff3)
	def test_train_stateful_02(self):
		if not self.test_model: return
		seq_len, batch_size = 4,4
		IS = reader.input_slicer(self.fa, self.mr1, self.gff3, stateful=True)
		# create models
		M = model.sleight_model('default', self.n_inputs, seq_len, self.n_outputs, n_neurons=80, \
			learning_rate=0.01, bidirectional=False, save_dir='test_model', \
			cell_type='lstm', stateful=batch_size)
		self.assertTrue(len(glob('%s*'%(M.save_file))) > 0)
		M.restore()
		# Vote
		OA = writer.output_aggregator(self.fa)
		for chrom in sorted(IS.FA.references):
			M.model.reset_states()
			for cb, xb, yb in IS.stateful_chrom_iter(chrom, seq_len, 1, batch_size=4):
				y_pred_batch = M.predict(xb)
				for c, x, y, yp in zip(cb, xb, yb, y_pred_batch):
					#for feature_index in range(len(y[0])):
					#	ys = ', '.join([str(y[bi][feature_index]) for bi in range(len(y))])
					#	yps = ', '.join([str(yp[bi][feature_index]) for bi in range(len(y))])
					#	if ys != yps:
					#		print("%s:%i-%i FI:%2i Y=[%s]  Y_PRED=[%s]"%(c[0], c[1], c[2], feature_index, ys, yps))
					#self.assertTrue(np.array_equal(y, yp))
					OA.vote(*c, array=yp, overwrite=True)
		# Compare
		out_lines = OA.write_gff3()
		self._compare_against_file(out_lines, self.gff3)
		if os.path.exists(M.save_dir):
			rmtree(M.save_dir)
	def test_train_cli_01(self):
		if not self.test_model: return
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', 'test_cli', \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'train', \
			'-B', '6', \
			'-A', self.gff3, \
			'-E', str(self.n_epoch), \
			'-r', str(self.learning_rate), \
			'-L', '15', \
			'-n', '60', \
			'-b', '-f']
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		self.assertTrue('Done' in splitOut[-2])
		#for f in glob('test_cli/*'): print f
		self.assertTrue(os.path.exists('test_cli/plain_s15x10_o68_unstranded_1xbilstm60_merge-concat_statefulF_learn%s_drop0.h5'%(str(self.learning_rate))))
		self.assertTrue(os.path.exists('test_cli/config.pkl'))
	def test_train_cli_02(self):
		if not self.test_model: return
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', 'test_cli', \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'classify', \
			'-O', 'test_cli/out.gff3']
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		#for so in splitOut: print so
		self.assertTrue('Done' in splitOut[-2])
		self.assertTrue(os.path.exists('test_cli/out.gff3'))
		F1 = open(self.gff3,'r')
		F2 = open('test_cli/out.gff3','r')
		for test_line, cli_line in zip(F1.readlines(), F2.readlines()):
			if cli_line[0] != '#':
				test_split = test_line.rstrip('\n').split('\t')
				test_split[1] = 'teamRNN'
				cli_split = cli_line.rstrip('\n').split('\t')
				self.assertEqual(test_split, cli_split)
		if os.path.exists('test_cli'):
			rmtree('test_cli')
	def test_stateful_cli_01(self):
		if not self.test_model: return
		out_dir, lr, sl, n = 'test_stateful_cli', '0.01', '4', '128'
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', out_dir, \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'-v', 'train', \
			'-B', '4', \
			'-A', self.gff3, \
			'-E', '300', \
			'-r', lr, \
			'-l', '2', \
			'-L', sl, \
			'-n', n, \
			'--stateful', \
			'-f']
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		self.assertTrue('Done' in splitOut[-2])
		#for f in glob('test_cli/*'): print f
		self.assertTrue(os.path.exists('%s/plain_s%sx10_o68_unstranded_2xlstm%s_stateful4_learn%s_drop0.h5'%(out_dir, sl, n, lr)))
		self.assertTrue(os.path.exists('%s/config.pkl'%(out_dir)))
	def test_stateful_cli_02(self):
		if not self.test_model: return
		out_dir = 'test_stateful_cli'
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', out_dir, \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'classify', \
			'-O', '%s/out.gff3'%(out_dir)]
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		#for so in splitOut: print so
		self.assertTrue('Done' in splitOut[-2])
		self.assertTrue(os.path.exists('%s/out.gff3'%(out_dir)))
		F1 = open(self.gff3,'r')
		F2 = open('%s/out.gff3'%(out_dir),'r')
		for test_line, cli_line in zip(F1.readlines(), F2.readlines()):
			if cli_line[0] != '#':
				test_split = test_line.rstrip('\n').split('\t')
				test_split[1] = 'teamRNN'
				cli_split = cli_line.rstrip('\n').split('\t')
				self.assertEqual(test_split, cli_split)
		if os.path.exists(out_dir):
			rmtree(out_dir)
	def test_stateful_cli_noTEMD_01(self):
		if not self.test_model: return
		n, out_dir, lr, sl = '256', 'test_stateful_cli', '0.001', '10'
		bsize, layers = '1', '1'
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', out_dir, \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'-v', 'train', \
			'-B', bsize, \
			'-A', self.gff3, \
			'-E', '300', \
			'-r', lr, \
			'-l', layers, \
			'-L', sl, \
			'-n', n, \
			'--every', '100', \
			'--noTEMD', \
			'--stateful', \
			'-f']
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		#for line in splitOut:
		#	for w in ('LOSS','atches','Last'):
		#		if w in line:
		#			print line
		#			break
		self.assertTrue('Done' in splitOut[-2])
		#for f in glob('test_cli/*'): print f
		self.assertTrue(os.path.exists('%s/plain_s%sx10_o66_unstranded_%sxlstm%s_stateful%s_learn%s_drop0.h5'%(out_dir, sl, layers, n, bsize, lr)))
		self.assertTrue(os.path.exists('%s/config.pkl'%(out_dir)))
	def test_stateful_cli_noTEMD_02(self):
		if not self.test_model: return
		out_dir = 'test_stateful_cli'
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', out_dir, \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'classify', \
			'-O', '%s/out.gff3'%(out_dir)]
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		#for so in splitOut: print so
		self.assertTrue('Done' in splitOut[-2])
		self.assertTrue(os.path.exists('%s/out.gff3'%(out_dir)))
		OF = open('%s/out.gff3'%(out_dir),'r').readlines()
		self._compare_against_file(OF, self.gff3, True)
		if os.path.exists(out_dir):
			rmtree(out_dir)
	def test_gff2array_noTEMD(self):
		###gff-version   3
		#Chr1    test    CDS     3       10      .       +       .       ID=team_0
		#Chr1    test    gene    3       10      .       +       .       ID=team_1
		#Chr1    test    exon    4       7       .       +       .       ID=team_2
		#Chr1    test    transposable_element    10      15      .       -       .       ID=team_3;Order=LTR;Superfamily=Gypsy
		#Chr2    test    CDS     2       15      .       -       .       ID=team_4
		#Chr2    test    gene    2       15      .       -       .       ID=team_5
		#Chr2    test    exon    3       7       .       -       .       ID=team_6
		#Chr2    test    exon    9       14      .       -       .       ID=team_7
		GI = reader.gff3_interval(self.gff3, out_dim=len(constants.gff3_f2i), force=True)
		res1 = GI.fetch('Chr1', 0, 15)
		tmp = np.zeros((15, self.n_outputs-2), dtype=np.bool)
		tmp[2:10,constants.gff3_f2i['+CDS']] = 1
		tmp[2:10,constants.gff3_f2i['+gene']] = 1
		tmp[3:7,constants.gff3_f2i['+exon']] = 1
		tmp[9:15,constants.gff3_f2i['-transposable_element']] = 1
		self.assertEqual(res1.shape, tmp.shape)
		for i in range(15):
			if not np.array_equal(res1[i], tmp[i]):
				print("At index %i"%(i))
				print("Code:",res1[i])
				print("Test:",tmp[i])
		self.assertTrue(np.array_equal(res1, tmp))
		res2 = GI.fetch('Chr2', 0, 18)
		tmp = np.zeros((18,self.n_outputs-2))
		tmp[1:15,constants.gff3_f2i['-CDS']] = 1
		tmp[1:15,constants.gff3_f2i['-gene']] = 1
		tmp[2:7,constants.gff3_f2i['-exon']] = 1
		tmp[8:14,constants.gff3_f2i['-exon']] = 1
		self.assertTrue(np.array_equal(res2, tmp))
	def test_stateful_cli_noTEMD_stranded_01(self):
		if not self.test_model: return
		n, out_dir, lr, sl = '256', 'test_stateful_cli', '0.01', '10'
		bsize, layers = '1', '1'
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', out_dir, \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'-v', 'train', \
			'-B', bsize, \
			'-A', self.gff3, \
			'-E', '300', \
			'-r', lr, \
			'-l', layers, \
			'-L', sl, \
			'-n', n, \
			'--every', '100', \
			'--noTEMD', \
			'--stateful', \
			'--stranded', \
			'-f']
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		#for line in splitOut:
		#	if 'LOSS' in line: print line
		self.assertTrue('Done' in splitOut[-2])
		self.assertTrue('Output aggregator expecting stranded data' in output)
		#for f in glob('test_cli/*'): print f
		self.assertTrue(os.path.exists('%s/plain_s%sx10_o66_stranded_%sxlstm%s_stateful%i_learn%s_drop0.h5'%(out_dir, sl, layers, n, int(bsize)*2, lr)))
		self.assertTrue(os.path.exists('%s/config.pkl'%(out_dir)))
	def test_stateful_cli_noTEMD_stranded_02(self):
		if not self.test_model: return
		out_dir = 'test_stateful_cli'
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', out_dir, \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'classify', \
			'-O', '%s/out.gff3'%(out_dir)]
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		#for so in splitOut: print so
		self.assertTrue('Done' in splitOut[-2])
		self.assertTrue(os.path.exists('%s/out.gff3'%(out_dir)))
		OF = open('%s/out.gff3'%(out_dir),'r').readlines()
		self._compare_against_file(OF, self.gff3, True)
		if os.path.exists(out_dir):
			rmtree(out_dir)
	def test_stateful_cli_noTEMD_stranded_dense2_01(self):
		if not self.test_model: return
		n, out_dir, lr, sl = '32', 'test_stateful_cli', '0.01', '10'
		bsize, layers = '1', '2'
		if os.path.exists(out_dir): rmtree(out_dir)
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', out_dir, \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'-v', 'train', \
			'-B', bsize, \
			'-A', self.gff3, \
			'-E', '300', \
			'-r', lr, \
			'-l', layers, \
			'--dense', '3', \
			'-L', sl, \
			'-n', n, \
			'--every', '100', \
			'--noTEMD', \
			'--stateful', \
			'--stranded', \
			'-f']
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		#print output
		splitOut = output.split('\n')
		self.assertTrue('Done' in splitOut[-2])
		#for f in glob('test_cli/*'): print f
		self.assertTrue(os.path.exists('%s/plain_s%sx10_o66_stranded_%sxlstm%s_stateful%i_learn%s_drop0.h5'%(out_dir, sl, layers, n, int(bsize)*2, lr)))
		self.assertTrue(os.path.exists('%s/config.pkl'%(out_dir)))
		self.assertTrue(os.path.exists('%s/training_output_raw.gff3'%(out_dir)))
		OF = open('%s/training_output_raw.gff3'%(out_dir),'r').readlines()
		self._compare_against_file(OF, self.gff3, True)
	def test_stateful_cli_noTEMD_stranded_dense2_02(self):
		if not self.test_model: return
		out_dir = 'test_stateful_cli'
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', out_dir, \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'classify', \
			'-O', '%s/out.gff3'%(out_dir)]
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		#for so in splitOut: print so
		self.assertTrue('Done' in splitOut[-2])
		self.assertTrue(os.path.exists('%s/out.gff3'%(out_dir)))
		OF = open('%s/out.gff3'%(out_dir),'r').readlines()
		self._compare_against_file(OF, self.gff3, True)
		if os.path.exists(out_dir):
			rmtree(out_dir)
	def test_stateful_cli_noTEMD_few_01(self):
		if not self.test_model: return
		n, out_dir, lr, sl = '256', 'test_stateful_cli', '0.001', '10'
		bsize, layers = '1', '1'
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', out_dir, \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'-v', 'train', \
			'-B', bsize, \
			'-A', self.gff3, \
			'-E', '250', \
			'-r', lr, \
			'-l', layers, \
			'-L', sl, \
			'-n', n, \
			'--every', '100', \
			'--noTEMD', \
			'--stateful', \
			'--fewer', \
			'-f']
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		self.assertTrue('Done' in splitOut[-2])
		#for f in glob('test_cli/*'): print f
		self.assertTrue(os.path.exists('%s/plain_s%sx10_o16_unstranded_%sxlstm%s_stateful%s_learn%s_drop0.h5'%(out_dir, sl, layers, n, bsize, lr)))
		self.assertTrue(os.path.exists('%s/config.pkl'%(out_dir)))
	def test_stateful_cli_noTEMD_few_02(self):
		if not self.test_model: return
		out_dir = 'test_stateful_cli'
		testArgs = ['teamRNN', \
			'-R', self.fa, \
			'-D', out_dir, \
			'-N', 'plain', \
			'-M', self.mr1, \
			'--max_fill', '0', \
			'--min_feat', '0', \
			'classify', \
			'-O', '%s/out.gff3'%(out_dir)]
		with patch('sys.argv', testArgs):
			teamRNN.main()
		output = logStream.getvalue()
		splitOut = output.split('\n')
		#for so in splitOut: print so
		self.assertTrue('Done' in splitOut[-2])
		self.assertTrue(os.path.exists('%s/out.gff3'%(out_dir)))
		OF = open('%s/out.gff3'%(out_dir),'r').readlines()
		self._compare_against_file(OF, self.gff3, True)
		if os.path.exists(out_dir):
			rmtree(out_dir)

def find_max(seq_len, offset, batch_size, hvd_size, chrom_len=20):
	full_len = seq_len+(batch_size-1)*offset
	return max([len(range(r*offset*batch_size, \
			chrom_len-full_len+1, \
			hvd_size*offset*batch_size)) for r in range(hvd_size)])

if __name__ == "__main__":
	unittest.main()
